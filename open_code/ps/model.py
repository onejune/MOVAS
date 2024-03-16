import os
import json
import time
import asyncio
import torch
import collections
from . import _ps
from .agent import Agent
from .name_utils import is_valid_qualified_name
from .index_batch_manager import IndexBatchManager
from .initializer import ZeroTensorInitializer
from .initializer import OneTensorInitializer
from .updater import EMATensorUpdater
from .embedding import EmbeddingOperator
from .distributed_tensor import DistributedTensor

class Model(object):
    def __init__(self, agent, module, experiment_name=None, model_version=None):
        if not isinstance(agent, Agent):
            raise TypeError(f"agent must be ps.Agent; {agent!r} is invalid")
        if not isinstance(module, torch.nn.Module):
            raise TypeError(f"module must be torch.nn.Module; {module!r} is invalid")
        if experiment_name is not None:
            if not isinstance(experiment_name, str) or not is_valid_qualified_name(experiment_name.strip()):
                raise TypeError(f"experiment_name must be valid qualified name; {experiment_name!r} is invalid")
            experiment_name = experiment_name.strip()
        if model_version is not None:
            if not isinstance(model_version, str):
                raise TypeError(f"model_version must be string; {model_version!r} is invalid")
            model_version = model_version.strip()
        self._agent = agent
        self._module = module
        self._experiment_name = experiment_name
        self._model_version = model_version
        self._tensors = []

    @property
    def agent(self):
        return self._agent

    @property
    def module(self):
        return self._module

    @property
    def experiment_name(self):
        return self._experiment_name

    @experiment_name.setter
    def experiment_name(self, value):
        if value is not None:
            if not isinstance(value, str) or not is_valid_qualified_name(value.strip()):
                raise TypeError(f"experiment_name must be valid qualified name; {value!r} is invalid")
            value = value.strip()
        if self._experiment_name is not None:
            raise RuntimeError(f"can not reset experiment_name {self._experiment_name!r} to {value!r}")
        self._experiment_name = value

    def _checked_get_experiment_name(self):
        if self._experiment_name is None:
            raise RuntimeError("experiment_name is not set")
        return self._experiment_name

    @property
    def model_version(self):
        return self._model_version

    @model_version.setter
    def model_version(self, value):
        if value is not None:
            if not isinstance(value, str):
                raise TypeError(f"model_version must be string; {value!r} is invalid")
            value = value.strip()
        if self._model_version is not None:
            raise RuntimeError(f"can not reset model_version {self._model_version!r} to {value!r}")
        self._model_version = value

    @property
    def training(self):
        return self._module.training

    def train(self):
        if not self.training:
            self._module.train()

    def eval(self):
        if self.training:
            self._module.eval()

    def _is_batch_norm(self, name, mod):
        if isinstance(mod, torch.nn.modules.batchnorm._BatchNorm):
            if isinstance(mod, torch.nn.SyncBatchNorm):
                message = f"module {name!r} is an instance of torch.nn.SyncBatchNorm, "
                message += "which is not supported by ps.DistributedTrainer"
                raise RuntimeError(message)
            return True
        return False

    def _configure_batch_norm(self, name, mod):
        if mod.running_mean is not None and getattr(mod.running_mean, 'initializer', None) is None:
            mod.running_mean.initializer = ZeroTensorInitializer()
        if mod.running_mean is not None and getattr(mod.running_mean, 'updater', None) is None:
            mod.running_mean.updater = EMATensorUpdater()
        if mod.running_var is not None and getattr(mod.running_var, 'initializer', None) is None:
            mod.running_var.initializer = OneTensorInitializer()
        if mod.running_var is not None and getattr(mod.running_var, 'updater', None) is None:
            mod.running_var.updater = EMATensorUpdater()
        if mod.weight is not None and getattr(mod.weight, 'initializer', None) is None:
            mod.weight.initializer = OneTensorInitializer()
        if mod.bias is not None and getattr(mod.bias, 'initializer', None) is None:
            mod.bias.initializer = ZeroTensorInitializer()

    def _configure_batch_norms(self):
        for name, mod in self.module.named_modules():
            if self._is_batch_norm(name, mod):
                self._configure_batch_norm(name, mod)

    def _collect_batch_norm(self, name, mod):
        running_mean = mod.running_mean
        running_mean_name = name + '.running_mean'
        running_var = mod.running_var
        running_var_name = name + '.running_var'
        if running_mean is None and running_var is None:
            return
        if running_mean is not None and running_var is not None:
            tensor1 = DistributedTensor(running_mean_name, running_mean)
            tensor2 = DistributedTensor(running_var_name, running_var)
            self._tensors.append(tensor1)
            self._tensors.append(tensor2)
            return
        message = "running_mean and running_var must be both None or both not None; "
        message += f"BatchNorm module {name!r} is invalid"
        raise RuntimeError(message)

    def _collect_embedding_operators(self):
        for name, mod in self.module.named_modules():
            if isinstance(mod, EmbeddingOperator):
                message = f"embedding operator {name!r} detected, "
                message += "please use ps.SparseModel instead of ps.Model as the wrapper"
                raise RuntimeError(message)

    def _collect_dense_parameters(self):
        for name, param in self.module.named_parameters():
            tensor = DistributedTensor(name, param)
            self._tensors.append(tensor)

    def _collect_dense_buffers(self):
        for name, mod in self.module.named_modules():
            if self._is_batch_norm(name, mod):
                self._collect_batch_norm(name, mod)

    def _collect_tensors(self):
        self._collect_embedding_operators()
        self._collect_dense_parameters()
        self._collect_dense_buffers()

    async def _init_tensors(self, trainer):
        futures = []
        for tensor in self._tensors:
            future = tensor._init_tensor(trainer)
            futures.append(future)
        await asyncio.gather(*futures)

    async def _pull_tensors(self, *, force_mode=False):
        futures = []
        for tensor in self._tensors:
            if not force_mode:
                # Pulling dense parameters in prediction mode is redundant.
                if not self.training and tensor.is_dense:
                    continue
            future = tensor._pull_tensor()
            futures.append(future)
        await asyncio.gather(*futures)

    async def _push_tensors(self, *, is_value=False, skip_no_grad=True):
        futures = []
        for tensor in self._tensors:
            future = tensor._push_tensor(is_value=is_value, skip_no_grad=skip_no_grad)
            futures.append(future)
        await asyncio.gather(*futures)

    async def _clear_tensors(self):
        pass

    async def _load_tensors(self, dir_path, *, keep_meta=False):
        futures = []
        for tensor in self._tensors:
            future = tensor._load_tensor(dir_path, keep_meta=keep_meta)
            futures.append(future)
        await asyncio.gather(*futures)

    async def _save_tensors(self, dir_path):
        futures = []
        for tensor in self._tensors:
            future = tensor._save_tensor(dir_path)
            futures.append(future)
        await asyncio.gather(*futures)

    def _get_full_class_name(self, obj):
        cls = obj.__class__
        name = '%s.%s' % (cls.__module__, cls.__name__)
        return name

    def _get_model_version(self):
        if self._model_version is not None:
            return self._model_version
        now = time.time()
        now += 8 * 3600
        tm = time.gmtime(now)
        ver = time.strftime('%Y%m%d%H', tm)
        return ver

    def _get_export_meta(self, path):
        meta_version = 1
        agent_class = self._get_full_class_name(self.agent)
        model_class = self._get_full_class_name(self)
        model_version = self._get_model_version()
        module_class = self._get_full_class_name(self.module)
        module_file = os.path.basename(path)
        experiment_name = self._checked_get_experiment_name()
        meta = {
            'meta_version' : meta_version,
            'agent_class' : agent_class,
            'model_class' : model_class,
            'model_version' : model_version,
            'module_class' : module_class,
            'module_file' : module_file,
            'experiment_name' : experiment_name,
        }
        return meta

    def _from_json_string(self, string):
        return json.loads(string, object_pairs_hook=collections.OrderedDict)

    def _as_json_string(self, obj):
        string = json.dumps(obj, separators=(',', ': '), indent=4)
        return string

    def _do_export(self, path):
        meta = self._get_export_meta(path)
        string = self._as_json_string(meta)
        self.module._meta = string
        scm = torch.jit.script(self.module)
        dir_path = os.path.dirname(path)
        _ps.ensure_local_directory(dir_path)
        class FakeStream(object):
            def write(self, data):
                _ps.stream_write_all(path, data)
        fout = FakeStream()
        torch.jit.save(scm, fout)
        data = (string + '\n').encode('utf-8')
        _ps.stream_write_all(path + '.json', data)

    def export(self, path):
        if not isinstance(path, str) or not path.strip():
            raise TypeError(f"path must be non-empty string; {path!r} is invalid")
        path = path.strip()
        if self._experiment_name is None:
            raise RuntimeError(f"experiment_name is not set; can not export to {path!r}")
        if path.endswith('.ptm'):
            pass
        elif path.endswith('/'):
            path += self._experiment_name + '.ptm'
        else:
            _, ext = os.path.splitext(path)
            if not ext:
                path = os.path.join(path, self._experiment_name + '.ptm')
            else:
                raise ValueError(f"path must be file path ends with .ptm or directory path endswith /; {path!r} is invalid")
        if self.training:
            message = "model is in training mode, can not export it; "
            message += "call the 'eval' method to set it in evaluation mode explicitly"
            raise RuntimeError(message)
        self.agent.barrier()
        asyncio.run(self._pull_tensors(force_mode=True))
        if self.agent.rank == 0:
            self._do_export(path)
        self.agent.barrier()

    def sync(self):
        self.agent.barrier()
        asyncio.run(self._pull_tensors(force_mode=True))
        self.agent.barrier()

    def __call__(self, *inputs):
        # Pulling dense parameters in prediction mode is redundant.
        if self.training:
            asyncio.run(self._pull_tensors())
        return self.module(*inputs)

    def _zero_grad(self):
        for tensor in self._tensors:
            tensor._zero_grad()

    @classmethod
    def _contains_embedding_operators(cls, module):
        for name, mod in module.named_modules():
            if isinstance(mod, EmbeddingOperator):
                return True
        return False

    @classmethod
    def wrap(cls, agent, module):
        if cls._contains_embedding_operators(module):
            return SparseModel(agent, module)
        else:
            return Model(agent, module)

class SparseModel(Model):
    def __init__(self, agent, module, experiment_name=None):
        super().__init__(agent, module, experiment_name)
        self._operators = []
        self._batch_manager = IndexBatchManager()

    def _collect_embedding_operators(self):
        for name, mod in self.module.named_modules():
            if isinstance(mod, EmbeddingOperator):
                tensor = DistributedTensor(name, mod)
                self._tensors.append(tensor)
                self._operators.append(tensor)
                mod._distributed_tensor = tensor

    async def _clear_tensors(self):
        futures = []
        for tensor in self._operators:
            future = tensor._sparse_tensor_clear()
            futures.append(future)
        await asyncio.gather(*futures)

    async def _sparse_tensors_export(self, path):
        futures = []
        for tensor in self._operators:
            # Call the ``_clean()`` method so that intermediate results
            # in the ps.EmbeddingOperator won't be serialized.
            tensor.item._clean()
            dir_path = path + '.msd/' + tensor.name + '.msm'
            future = tensor._sparse_tensor_export(dir_path)
            futures.append(future)
        await asyncio.gather(*futures)

    async def _sparse_tensors_prune_small(self, epsilon):
        futures = []
        for tensor in self._operators:
            future = tensor._sparse_tensor_prune_small(epsilon)
            futures.append(future)
        await asyncio.gather(*futures)

    async def _sparse_tensors_prune_old(self, max_age):
        futures = []
        for tensor in self._operators:
            future = tensor._sparse_tensor_prune_old(max_age)
            futures.append(future)
        await asyncio.gather(*futures)

    def _do_export(self, path):
        asyncio.run(self._sparse_tensors_export(path))
        super()._do_export(path)

    def _get_export_meta(self, path):
        sparse_data_dir = os.path.basename(path) + '.msd'
        sparse_tensors = []
        for tensor in self._operators:
            name = tensor.name
            data_dir = tensor.name + '.msm'
            partition_count = tensor._handle.partition_count
            sparse_tensor = {
                'name' : name,
                'data_dir' : data_dir,
                'partition_count' : partition_count,
            }
            sparse_tensors.append(sparse_tensor)
        meta = super()._get_export_meta(path)
        meta['sparse_data_dir'] = sparse_data_dir
        meta['sparse_tensors'] = sparse_tensors
        return meta

    def prune_small(self, epsilon=1e-6):
        if not isinstance(epsilon, float) or epsilon < 0.0:
            if epsilon != 0:
                raise TypeError(f"epsilon must be non-negative float or 0; {epsilon!r} is invalid")
        self.agent.barrier()
        if self.agent.rank == 0:
            asyncio.run(self._sparse_tensors_prune_small(epsilon))
        self.agent.barrier()

    def prune_old(self, max_age):
        if not isinstance(max_age, int) or max_age <= 0:
            raise TypeError(f"max_age must be positive integer; {max_age!r} is invalid")
        self.agent.barrier()
        if self.agent.rank == 0:
            asyncio.run(self._sparse_tensors_prune_old(max_age))
        self.agent.barrier()

    def _execute_combine(self, ndarrays):
        self._batch_manager.clear()
        for tensor in self._operators:
            tensor.item._combine(ndarrays, self._batch_manager)

    def _execute_pull(self):
        asyncio.run(self._pull_tensors())

    def _execute_compute(self):
        for tensor in self._operators:
            tensor.item._compute()

    def __call__(self, ndarrays):
        self._execute_combine(ndarrays)
        self._execute_pull()
        self._execute_compute()
        fake_input = torch.tensor(0.0)
        x = self.module(fake_input)
        return x
