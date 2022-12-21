from dataclasses import dataclass

from parsl.app.app import python_app
from parsl.data_provider.files import File

from flower.execution import Container, ModelExecutionDefinition
from flower.sampling.base import BaseWalker
from flower.utils import _new_file


def optimize_geometry(
        device,
        ncores,
        state,
        parameters,
        load_calculator,
        plumed_input='',
        inputs=[],
        outputs=[],
        ):
    import os
    import tempfile
    import torch
    import numpy as np
    from ase.optimize.precon import Exp, PreconLBFGS
    from ase.constraints import ExpCellFilter
    from ase.io import read
    from ase.io.extxyz import write_extxyz
    torch.set_default_dtype(torch.float64) # optimization always in double
    if device == 'cpu':
        torch.set_num_threads(ncores)

    pars = parameters
    np.random.seed(pars.seed)
    torch.manual_seed(pars.seed)
    atoms = state.copy()
    atoms.calc = load_calculator(inputs[0].filepath, device, dtype='float64')
    preconditioner = Exp(A=3) # from ASE docs
    if parameters.optimize_cell: # include cell DOFs in optimization 
        try: # some models do not have stress support; prevent full cell opt!
            stress = atoms.get_stress()
        except Exception as e:
            raise ValueError('cell optimization requires stress support in model')
        dof = ExpCellFilter(atoms, mask=[True] * 6)
    else:
        dof = atoms
    #optimizer = SciPyFminCG(
    #        dof,
    #        trajectory=str(path_traj),
    #        )
    tmp = tempfile.NamedTemporaryFile(delete=False, mode='w+')
    tmp.close()
    path_traj = tmp.name # dummy log file
    optimizer = PreconLBFGS(
            dof,
            precon=preconditioner,
            use_armijo=True,
            trajectory=path_traj,
            )

    tag = 'safe'
    try:
        optimizer.run(fmax=parameters.fmax)
    except:
        tag = 'unsafe'
        pass
    atoms.calc = None
    with open(outputs[0], 'w') as f:
        trajectory = read(path_traj)
        write_extxyz(f, trajectory)
    os.unlink(path_traj)
    return atoms, tag


@dataclass
class OptimizationParameters:
    optimize_cell: bool = True # include cell DOFs in optimization
    fmax         : float = 1e-2 # max residual norm of forces before termination
    seed               : int = 0 # seed for randomized initializations


class OptimizationWalker(BaseWalker):
    parameters_cls = OptimizationParameters

    @classmethod
    def create_apps(cls, context):
        executor_label = context[ModelExecutionDefinition].executor_label
        device = context[ModelExecutionDefinition].device
        ncores = context[ModelExecutionDefinition].ncores
        path = context.path

        app_optimize = python_app(
                optimize_geometry,
                executors=[executor_label],
                )
        def optimize_wrapped(
                state,
                parameters,
                model=None,
                keep_trajectory=False,
                **kwargs,
                ):
            assert model is not None # model is required
            assert 'float64' in model.deploy_future.keys() # has to be deployed
            inputs = [model.deploy_future['float64']]
            outputs = [File(_new_file(path, 'traj_', '.xyz'))]
            result = app_optimize(
                    device,
                    ncores,
                    state,
                    parameters,
                    model.load_calculator, # load function
                    inputs=inputs,
                    outputs=outputs,
                    )
            if not keep_trajectory:
                dataset = None
            else:
                dataset = Dataset(context, data_future=result.outputs[0])
            return result, dataset

        context.register_app(cls, 'propagate', optimize_wrapped)
        super(OptimizationWalker, cls).create_apps(context)