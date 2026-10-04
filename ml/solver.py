"""Minimal primal-only HiGHS MILP adapter, pinned by requirements.txt.

An intermittent native fault on this host occurred while SciPy's Windows MILP
wrapper read LP basis data after a MIP solve. This is not proof of a SciPy bug. MIPs do
not have meaningful LP shadow prices. This adapter uses the same bundled HiGHS
solver and reads only validated primal values, objective and status. It does not
patch dependencies or fabricate a successful solve.
"""
import bootstrap
import numpy as np
from scipy.optimize import OptimizeResult
from scipy.sparse import csc_matrix, vstack
from scipy.optimize._highspy import _core as h


def solve_milp(c, integrality, bounds, constraints, options=None):
    constraints=constraints if isinstance(constraints,(list,tuple)) else [constraints]
    c=np.asarray(c,dtype=np.float64)
    A=vstack([csc_matrix(q.A) for q in constraints],format='csc')
    lower=np.concatenate([np.broadcast_to(q.lb,(q.A.shape[0],)) for q in constraints]).astype(np.float64)
    upper=np.concatenate([np.broadcast_to(q.ub,(q.A.shape[0],)) for q in constraints]).astype(np.float64)
    lp=h.HighsLp();lp.num_col_=len(c);lp.num_row_=A.shape[0]
    lp.col_cost_=c;lp.col_lower_=np.broadcast_to(bounds.lb,c.shape).astype(np.float64);lp.col_upper_=np.broadcast_to(bounds.ub,c.shape).astype(np.float64)
    lp.row_lower_=lower;lp.row_upper_=upper
    lp.a_matrix_.format_=h.MatrixFormat.kColwise
    lp.a_matrix_.num_col_=len(c);lp.a_matrix_.num_row_=A.shape[0]
    lp.a_matrix_.start_=A.indptr.astype(np.int32);lp.a_matrix_.index_=A.indices.astype(np.int32);lp.a_matrix_.value_=A.data.astype(np.float64)
    lp.integrality_=[h.HighsVarType(int(v)) for v in integrality]
    solver=h._Highs(); settings=h.HighsOptions()
    settings.output_flag=False;settings.log_to_console=False;settings.threads=1;settings.parallel='off'
    for key,value in (options or {}).items():
        if key in ('threads','parallel'):continue
        if hasattr(settings,key):setattr(settings,key,value)
    if solver.passOptions(settings)==h.HighsStatus.kError or solver.passModel(lp)==h.HighsStatus.kError:
        return OptimizeResult(success=False,message='HiGHS model/options rejected',x=None,fun=None)
    solver.run(); status=solver.getModelStatus();message=solver.modelStatusToString(status)
    if status!=h.HighsModelStatus.kOptimal:
        return OptimizeResult(success=False,message=message,x=None,fun=None,status=int(status))
    solution=solver.getSolution()
    x=np.array(solution.col_value,dtype=float,copy=True)
    if len(x)!=len(c) or not np.isfinite(x).all():
        return OptimizeResult(success=False,message='HiGHS returned invalid primal solution',x=None,fun=None)
    # Independently check feasibility before reporting optimal success.
    activity=A@x;tol=2e-5
    if (activity<lower-tol).any() or (activity>upper+tol).any() or (x<np.asarray(bounds.lb)-tol).any() or (x>np.asarray(bounds.ub)+tol).any():
        return OptimizeResult(success=False,message='HiGHS primal feasibility validation failed',x=None,fun=None)
    mask=np.asarray(integrality)>0
    if np.max(np.abs(x[mask]-np.round(x[mask])),initial=0)>tol:
        return OptimizeResult(success=False,message='HiGHS integrality validation failed',x=None,fun=None)
    info=solver.getInfo()
    return OptimizeResult(success=True,message=message,x=x,fun=float(c@x),status=int(status),mip_gap=float(info.mip_gap),mip_dual_bound=float(info.mip_dual_bound))
