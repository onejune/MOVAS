import abc
import operator
import torch
import numpy

class TensorUpdater(abc.ABC):
    def __init__(self, learning_rate):
        self.learning_rate = learning_rate

    @property
    def learning_rate(self):
        return self.__learning_rate

    @learning_rate.setter
    def learning_rate(self, value):
        if not isinstance(value, float) or value <= 0.0:
            message = "learning_rate must be positive float; "
            message += "%r is invalid" % value
            raise ValueError(message)
        self.__learning_rate = value

    def get_dense_data_shape(self, tensor):
        data_shape = tensor.item.shape
        if len(data_shape) == 1:
            return data_shape[0], 1
        else:
            return data_shape

    def get_dense_state_shape(self, tensor):
        normalized = self.get_dense_data_shape(tensor)
        return self.get_state_shape(tensor, normalized)

    def get_sparse_slice_data_shape(self, tensor):
        return tensor.item._checked_get_embedding_size(),

    def get_sparse_slice_state_shape(self, tensor):
        normalized = self.get_sparse_slice_data_shape(tensor)
        return self.get_state_shape(tensor, normalized)

    @property
    def states_per_param(self):
        return None

    def get_state_shape(self, tensor, data_shape):
        num = self.states_per_param
        if num is not None and num > 0:
            state_shape = list(data_shape)
            state_shape[-1] *= num
            return tuple(state_shape)
        return None

    def get_state_tensor(self, state, index):
        num = self.states_per_param
        dim = state.shape[-1]
        subscript = list(slice(None) for d in state.shape)
        subscript[-1] = slice(dim // num * index, dim // num * (index + 1))
        subscript = tuple(subscript)
        tensor = operator.getitem(state, subscript)
        return tensor

    def get_dense_state_tensor(self, state, index):
        return self.get_state_tensor(state, index)

    def get_sparse_state_tensor(self, state, index):
        return self.get_state_tensor(state, index)

    @abc.abstractmethod
    def __repr__(self):
        return '%s(%r)' % (self.__class__.__name__, self.learning_rate)

    @abc.abstractmethod
    def update_dense(self, name, param, grad, state):
        raise NotImplementedError

    @abc.abstractmethod
    def update_sparse(self, name, param, grad, state, indices, keys):
        raise NotImplementedError

    def __call__(self, name, param, grad, state, indices, keys):
        param = torch.from_numpy(param)
        grad = torch.from_numpy(grad)
        if state is not None:
            state = torch.from_numpy(state)
        if indices is None:
            self.update_dense(name=name, param=param, grad=grad, state=state)
        else:
            indices = torch.from_numpy(indices.view(numpy.int64))
            keys = torch.from_numpy(keys.view(numpy.int64))
            self.update_sparse(name=name, param=param, grad=grad, state=state, indices=indices, keys=keys)

class SGDTensorUpdater(TensorUpdater):
    def __repr__(self):
        return super().__repr__()

    def update_dense(self, name, param, grad, state):
        param -= self.learning_rate * grad

    def update_sparse(self, name, param, grad, state, indices, keys):
        param[indices] -= self.learning_rate * grad

class AdaGradTensorUpdater(TensorUpdater):
    def __init__(self, learning_rate, float_stable_eps=0.0, l2=0.0):
        super().__init__(learning_rate)
        if not isinstance(float_stable_eps, float) or float_stable_eps < 0.0:
            message = "float_stable_eps must be non-negative float; "
            message += "%r is invalid" % float_stable_eps
            raise ValueError(message)
        if not isinstance(l2, float) or l2 < 0.0:
            message = "l2 must be non-negative float; "
            message += "%r is invalid" % l2
            raise ValueError(message)
        self.__float_stable_eps = float_stable_eps
        self.__l2 = l2

    @property
    def float_stable_eps(self):
        return self.__float_stable_eps

    @property
    def l2(self):
        return self.__l2

    def __repr__(self):
        return '%s(%r, %r, %r)' % (self.__class__.__name__,
                                   self.learning_rate,
                                   self.float_stable_eps,
                                   self.l2)

    @property
    def states_per_param(self):
        return 1

    def update_dense(self, name, param, grad, state):
        square_sum = self.get_dense_state_tensor(state, 0)
        grad_tmp = grad + self.l2 * param
        square_sum += grad_tmp * grad_tmp
        param -= self.learning_rate * grad_tmp / (square_sum + self.float_stable_eps).sqrt()

    def update_sparse(self, name, param, grad, state, indices, keys):
        square_sum = self.get_sparse_state_tensor(state, 0)
        grad_tmp = grad + self.l2 * param[indices]
        square_sum[indices] += grad_tmp * grad_tmp
        param[indices] -= self.learning_rate * grad_tmp / (square_sum[indices] + self.float_stable_eps).sqrt()

class AdamTensorUpdater(TensorUpdater):
    def __init__(self, learning_rate, beta1=0.9, beta2=0.999, epsilon=1e-8):
        super().__init__(learning_rate)
        if not isinstance(beta1, float) or beta1 < 0.0:
            message = "beta1 must be non-negative float; "
            message += "%r is invalid" % beta1
            raise ValueError(message)
        if not isinstance(beta2, float) or beta2 < 0.0:
            message = "beta2 must be non-negative float; "
            message += "%r is invalid" % beta2
            raise ValueError(message)
        if not isinstance(epsilon, float) or epsilon < 0.0:
            message = "epsilon must be non-negative float; "
            message += "%r is invalid" % epsilon
            raise ValueError(message)
        self.__beta1 = beta1
        self.__beta2 = beta2
        self.__epsilon = epsilon

    def __repr__(self):
        return '%s(%r, %r, %r, %r)' % (self.__class__.__name__,
                                       self.learning_rate,
                                       self.__beta1,
                                       self.__beta2,
                                       self.__epsilon)

    @property
    def states_per_param(self):
        return 2

    def update_dense(self, name, param, grad, state):
        m = self.get_dense_state_tensor(state, 0)
        v = self.get_dense_state_tensor(state, 1)
        m[...] = self.__beta1 * m + (1.0 - self.__beta1) * grad
        v[...] = self.__beta2 * v + (1.0 - self.__beta2) * grad * grad
        param -= self.learning_rate * m / (v.sqrt() + self.__epsilon)

    def update_sparse(self, name, param, grad, state, indices, keys):
        m = self.get_sparse_state_tensor(state, 0)
        v = self.get_sparse_state_tensor(state, 1)
        m[indices] = self.__beta1 * m[indices] + (1.0 - self.__beta1) * grad
        v[indices] = self.__beta2 * v[indices] + (1.0 - self.__beta2) * grad * grad
        param[indices] -= self.learning_rate * m[indices] / (v[indices].sqrt() + self.__epsilon)

class FTRLTensorUpdater(TensorUpdater):
    def __init__(self, l1=1.0, l2=120.0, alpha=0.5, beta=1.0):
        if not isinstance(l1, float) or l1 < 0.0:
            message = "l1 must be non-negative float; "
            message += "%r is invalid" % l1
            raise ValueError(message)
        if not isinstance(l2, float) or l2 < 0.0:
            message = "l2 must be non-negative float; "
            message += "%r is invalid" % l2
            raise ValueError(message)
        if not isinstance(alpha, float) or alpha < 0.0:
            message = "alpha must be non-negative float; "
            message += "%r is invalid" % alpha
            raise ValueError(message)
        if not isinstance(beta, float) or beta < 0.0:
            message = "beta must be non-negative float; "
            message += "%r is invalid" % beta
            raise ValueError(message)
        super().__init__(l1)
        self.__l1 = l1
        self.__l2 = l2
        self.__alpha = alpha
        self.__beta = beta

    def __repr__(self):
        return '%s(%r, %r, %r, %r)' % (self.__class__.__name__,
                                       self.__l1,
                                       self.__l2,
                                       self.__alpha,
                                       self.__beta)

    @property
    def states_per_param(self):
        return 2

    def update_dense(self, name, param, grad, state):
        n = self.get_dense_state_tensor(state, 0)
        z = self.get_dense_state_tensor(state, 1)
        grad_square = grad * grad
        sigma = ((n + grad_square).sqrt() - n.sqrt()) / self.__alpha
        z[...] = z + grad - sigma * param
        n[...] = n + grad_square
        param[...] = torch.where(torch.abs(z) <= self.__l1,
            torch.tensor(0.0),
            -(z - torch.sign(z) * self.__l1) / ((self.__beta + n.sqrt()) / self.__alpha + self.__l2))

    def __sign(self, x):
        return torch.where(x > 0.0, torch.tensor(1.0), torch.tensor(-1.0))

    def update_sparse(self, name, param, grad, state, indices, keys):
        n = self.get_sparse_state_tensor(state, 0)
        z = self.get_sparse_state_tensor(state, 1)
        grad_square = grad * grad
        sigma = ((n[indices] + grad_square).sqrt() - n[indices].sqrt()) / self.__alpha
        z[indices] = z[indices] + grad - sigma * param[indices]
        n[indices] = n[indices] + grad_square
        x = torch.tensor(0.0)
        y = -(z[indices] - self.__sign(z[indices]) * self.__l1) / ((self.__beta + n[indices].sqrt()) / self.__alpha + self.__l2)
        condition = torch.abs(z[indices]) <= self.__l1
        param[indices] = torch.where(condition, x, y)

# Exponential Moving Average tensor updater
# Useful for running_mean and running_var of BatchNorm operators.
# This is a very special updater.
class EMATensorUpdater(TensorUpdater):
    def __init__(self, momentum=0.1):
        if not isinstance(momentum, float) or momentum <= 0.0 or momentum >= 1.0:
            message = "momentum must be positive float less than 1.0; "
            message += "%r is invalid" % momentum
            raise ValueError(message)
        super().__init__(momentum)

    @property
    def momentum(self):
        return self.learning_rate

    def __repr__(self):
        return super().__repr__()

    def update_dense(self, name, param, grad, state):
        param[...] = (1 - self.momentum) * param + self.momentum * grad

    def update_sparse(self, name, param, grad, state, indices, keys):
        param[indices] = (1 - self.momentum) * param[indices] + self.momentum * grad
