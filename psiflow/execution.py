from __future__ import annotations # necessary for type-guarding class methods
from typing import Optional, Callable, Union
import typeguard
from dataclasses import dataclass
from pathlib import Path
import logging

from parsl.dataflow.memoization import id_for_memo
from parsl.data_provider.files import File

from parsl.executors import HighThroughputExecutor, ThreadPoolExecutor
from parsl.data_provider.files import File
from parsl.config import Config


logger = logging.getLogger(__name__) # logging per module
logger.setLevel(logging.INFO)


class ExecutionDefinition:
    pass


@dataclass
class TrainingExecutionDefinition(ExecutionDefinition):
    label : str = 'training'
    device: str = 'cuda'
    dtype : str = 'float32'
    ncores: int = None # depends on GPU node architecture
    walltime: float = 3600


@dataclass
class ModelExecutionDefinition(ExecutionDefinition):
    label : str = 'model'
    device: str = 'cpu'
    ncores: int = None
    dtype : str = 'float32'


@dataclass
class ReferenceExecutionDefinition(ExecutionDefinition):
    device     : str = 'cpu'
    label      : str = 'reference'
    ncores     : int = None
    mpi_command: Optional[Callable] = lambda x: f'mpirun -np {x} '
    cp2k_exec  : str = 'cp2k.psmp' # default command for CP2K Reference
    time_per_singlepoint: float = 20


@typeguard.typechecked
class ExecutionContext:

    def __init__(
            self,
            config: Config,
            path: Union[Path, str],
            enable_logging: bool = True,
            ) -> None:
        self.config = config
        Path.mkdir(Path(path), parents=True, exist_ok=True)
        self.path = Path(path)
        self.executor_labels = [e.label for e in config.executors]
        self.execution_definitions = {}
        self._apps = {}
        self.file_index = {}
        assert 'default' in self.executor_labels
        logging.basicConfig(format='%(name)s - %(message)s')
        logging.getLogger('parsl').setLevel(logging.WARNING)

    def __getitem__(
            self,
            definition_class: type[ExecutionDefinition],
            ) -> ExecutionDefinition:
        assert definition_class in self.execution_definitions.keys()
        return self.execution_definitions[definition_class]

    def register(self, execution: ExecutionDefinition) -> None:
        assert execution.label in self.executor_labels
        key = execution.__class__
        if execution.device == 'cpu': # check whether cores are available
            found = False
            for executor in self.config.executors:
                if executor.label == execution.label:
                    if execution.ncores is None:
                        if type(executor) == HighThroughputExecutor:
                            execution.ncores = int(executor.cores_per_worker)
                        elif type(executor) == ThreadPoolExecutor:
                            execution.ncores = 1
                    else:
                        if type(executor) == HighThroughputExecutor:
                            assert executor.cores_per_worker == execution.ncores
        assert key not in self.execution_definitions.keys()
        self.execution_definitions[key] = execution

    def apps(self, container, app_name: str) -> Callable:
        if container not in self._apps.keys():
            container.create_apps(self)
        assert app_name in self._apps[container].keys()
        return self._apps[container][app_name]

    def register_app(
            self,
            container, # type hints fail to allow Container subclasses?
            app_name: str,
            app: Callable,
            ) -> None:
        if container not in self._apps.keys():
            self._apps[container] = {}
        assert app_name not in self._apps[container].keys()
        self._apps[container][app_name] = app

    def new_file(self, prefix: str, suffix: str) -> File:
        assert prefix[-1] == '_'
        assert suffix[0]  == '.'
        key = (prefix, suffix)
        if key not in self.file_index.keys():
            self.file_index[key] = 0
        padding = 6
        assert self.file_index[key] < (16 ** padding)
        identifier = '{0:0{1}x}'.format(self.file_index[key], padding)
        self.file_index[key] += 1
        return File(str(self.path / (prefix + identifier + suffix)))


@typeguard.typechecked
class Container:

    def __init__(self, context: ExecutionContext) -> None:
        self.context = context

    @staticmethod
    def create_apps(context: ExecutionContext):
        raise NotImplementedError


@id_for_memo.register(File)
def id_for_memo_file(file: File, output_ref=False):
    return bytes(file.filepath, 'utf-8')
