class SessionBuilder(object):
    def __init__(self,
                 local=False,
                 batch_size=100,
                 worker_count=1,
                 server_count=1,
                 worker_cpu=1,
                 server_cpu=1,
                 worker_memory='5G',
                 server_memory='5G',
                 coordinator_memory='5G',
                 omp_num_threads=None,
                 app_name='PySparkNotebook',
                 spark_master=None,
                 deploy_mode='cluster',
                 log_level='WARN'):
        self.local = local
        self.batch_size = batch_size
        self.worker_count = worker_count
        self.server_count = server_count
        self.worker_cpu = worker_cpu
        self.server_cpu = server_cpu
        self.worker_memory = worker_memory
        self.server_memory = server_memory
        self.coordinator_memory = coordinator_memory
        self.omp_num_threads = omp_num_threads
        self.app_name = app_name
        self.spark_master = spark_master
        self.deploy_mode = deploy_mode
        self.log_level = log_level

    def _get_executor_count(self):
        num = self.worker_count + self.server_count
        return num

    def _config_app_name(self, builder):
        builder.appName(self.app_name)

    def _config_spark_master(self, builder):
        if self.local:
            master = 'local[%d]' % self._get_executor_count()
            builder.master(master)
        else:
            if self.spark_master is not None:
                builder.master(self.spark_master)
            if self.deploy_mode is not None:
                builder.config('deploy-mode', self.deploy_mode)

    def _config_batch_size(self, builder):
        builder.config('spark.sql.execution.arrow.maxRecordsPerBatch', str(self.batch_size))

    def _config_resources(self, builder):
        from . import job_utils
        builder.config('spark.driver.memory', self.coordinator_memory)
        executor_memory = job_utils.merge_storage_size(self.worker_memory, self.server_memory)
        builder.config('spark.executor.memory', executor_memory)
        builder.config('spark.executor.instances', str(self._get_executor_count()))
        executor_cores = max(self.worker_cpu, self.server_cpu)
        builder.config('spark.executor.cores', str(executor_cores))
        omp_num_threads = self.omp_num_threads
        if omp_num_threads is None:
            omp_num_threads = executor_cores
        builder.config('spark.executorEnv.OMP_NUM_THREADS', str(omp_num_threads))

    def _add_extra_configs(self, builder):
        builder.config('spark.python.worker.reuse', 'true')
        builder.config('spark.shuffle.service.enabled', 'false')
        builder.config('spark.sql.execution.arrow.pyspark.enabled', 'true')
        builder.config('spark.task.maxFailures', '1')
        builder.config('spark.yarn.maxAppAttempts', '1')
        builder.config('spark.scheduler.minRegisteredResourcesRatio', '1.0')
        builder.config('spark.scheduler.maxRegisteredResourcesWaitingTime', '1800s')

    def build(self):
        import pyspark
        builder = pyspark.sql.SparkSession.builder
        self._config_app_name(builder)
        self._config_spark_master(builder)
        self._config_batch_size(builder)
        self._config_resources(builder)
        self._add_extra_configs(builder)
        spark_session = builder.getOrCreate()
        spark_context = spark_session.sparkContext
        spark_context.setLogLevel(self.log_level)
        return spark_session

def get_session(*args, **kwargs):
    builder = SessionBuilder(*args, **kwargs)
    spark_session = builder.build()
    return spark_session
