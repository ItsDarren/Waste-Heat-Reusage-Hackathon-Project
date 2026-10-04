# Heat Commons

**Decision software for turning data-center waste heat into community value.**

Built for the NYU hackathon's Grundfos heat-reuse challenge by **Darren, Nandan, Jason and Brian**. Heat Commons helps a heat-network planner compare candidate users, forecast supply and demand, and allocate heat within physical and service constraints. It compares **111 8th Avenue, NYC** with the **proposed Lake Hawkeye site in Lansing, New York**.

**All operational data and evaluation are synthetic. The district is schematic. No physical equipment is controlled.**

## Start here

| For judges | Open |
|---|---|
| Three-minute interactive presentation | [presentation/PRESENT.html](presentation/PRESENT.html) |
| Separate demo video | [media/Demo Video.mp4](media/Demo%20Video.mp4) |
| Four-person script | [PDF](presentation/TEAM_SCRIPT.pdf) · [plain text](presentation/TEAM_SCRIPT.txt) · [quick cues](presentation/QUICK_CUES.pdf) |
| Evidence and technical guide | [docs/README.html](docs/README.html) |
| System proposal and assumptions | [Proposal](baseline/documents/Heat_Commons_Proposal.pdf) · [Assumptions and sources](baseline/documents/Heat_Commons_Assumptions_Sources.pdf) |

Download and extract the repository, then open `presentation/PRESENT.html` in Chrome or Edge. It needs **no server, Python installation or internet connection**. Use its arrows or the keyboard to advance nine scenes; **Presenter view** opens the matching private script. The three-minute presentation does not play the separate demo video.

GitHub's file viewer does not execute interactive HTML. Use **Code → Download ZIP**, extract it, and open the local file.

The presentation contains saved outputs from the Python models and optimizer. The application below runs fresh inference and optimization with the included saved models.

## Run the application

Use **Python 3.12**. From the repository root:

**Windows PowerShell** - direct environment commands, no activation required:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe run.py serve
```

**macOS / Linux**:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python run.py serve
```

Open **http://127.0.0.1:8765**. The server binds to this computer only; stop it with `Ctrl+C`. Saved models and the synthetic dataset are included, so starting the app does not retrain. Keep the dependency versions pinned: saved estimators and the solver adapter depend on them.

## What the prototype does

- Forecasts the next 24 hours of electrical load, recoverable heat and customer demand, with nominal 90% uncertainty intervals.
- Allocates heat with a shared-equipment optimizer, accounting for temperatures, capacity, storage, network losses, backup and community-service requirements.
- Tests seasons, source outages, electricity prices, cold demand, disconnected customers and inadequate source temperature in a synchronized 3D explorer.
- Compares forecasting and dispatch against simple baselines, preserves unfavorable results, and exposes physical and accounting checks.

The rolling backtest executes one hour, updates observations and storage, then replans. The explorer's individually selected dates are independent 24-hour plans.

## What the saved evidence shows

| Synthetic evaluation | NYC | Lansing |
|---|---:|---:|
| Facility-load MAE reduction versus best simple test baseline | 22.1% | 20.6% |
| Observed coverage of nominal 90% facility intervals | 92.3% | 90.6% |
| Forecast-controller operating margin versus rule controller | **−2.43%** | **−2.31%** |

Better forecasts did **not** produce a higher aggregate operating margin. Rule control remains the reference. Dispatch evaluation covers **504 unseen hours per site**, across seasonal and stress windows. The saved record contains **3,024 executed controller-hours with zero checked physical violations**. These are simulation results, not field certification. Operating margin excludes capital, fixed O&M, financing and tax; customer backup costs are reported separately.

A **separate annual screening model** conditionally favors NYC: **12.50 GWh/year** of useful heat, **$9.07M** installed capital and **+$0.90M** NPV over 25 years at 7%, versus **5.24 GWh/year**, **$11.71M** and **−$12.52M** for the modeled Lansing network. These annual figures are not extrapolated from the short controller backtest.

See [results](docs/results.md), [forecast metadata](artifacts/forecast_metadata.json), [dispatch backtest](artifacts/backtest.json) and [annual results](baseline/model/results.json).

## Verify and reproduce

After installing dependencies, run from the repository root. On Windows, replace `python` below with `.venv\Scripts\python.exe`; on macOS/Linux, use the activated environment:

```sh
python run.py test
python verify_project.py
python -m ml.audit
```

To regenerate data, trained models or dispatch evidence:

```sh
python run.py data
python run.py train
python run.py backtest
```

Training rewrites model/evaluation artifacts, including the detailed horizon-and-season metrics and prediction files omitted from this compact repository. Backtesting rewrites its summary and hourly records. Record hashes and results before comparing regenerated outputs. The original annual model is reproducible with `python baseline/model/model.py`; its sensitivity and robustness sources are also included.

For external CSV inputs, see [ml/CSV_INGESTION.md](ml/CSV_INGESTION.md). Inputting measurements does not turn this synthetically trained model into validated field intelligence.

## Code and evidence

| Location | Purpose |
|---|---|
| `ml/` | Data generation, causal forecasts, inference, constrained dispatch and independent accounting audit |
| `web/`, `server.py` | Interactive application and local API |
| `tests/` | Physical, causal and integration tests |
| `artifacts/` | Saved model, dataset, evaluation, hourly dispatch and stress evidence |
| `baseline/model/` | Annual heat-network physics, assumptions, economics and reproduction sources |
| `baseline/documents/` | System proposal and cited assumptions |
| `presentation/`, `media/` | Judge-facing presentation, team script and separate demo |

## Upload readiness

Run `python check_upload.py` from the repository root to check the file count, file sizes and final presentation/video hashes. This package is prepared for **fewer than 100 files**, with **every file below 25 MiB**; Git LFS is not needed.

Create a fresh public GitHub repository and upload the **contents of this folder** so `README.md`, `run.py`, `presentation/` and the other directories appear at its root. Upload the prepared files before creating a virtual environment or regenerating artifacts. Keep local environments and caches out of the upload.

## Boundaries and next steps

The **2.5 MW recovery tranche is an assumed thermal limit**, not measured facility electricity. Heat-pump output includes compressor electricity; pipe losses and other electrical loads are counted separately. Customer cohorts, temperatures, routes, costs, tariffs and carbon factors are assumptions. The proposed system retains independent data-center cooling and customer backup.

Field deployment would require measured source and demand data, source rights, compatible customers, surveyed routes, vendor performance maps, tariff quotations, community consent, enforceable tenant pass-through and independent engineering review. No signed customers, surveyed digital twin, guaranteed return or site-validated accuracy is claimed.

The [assumptions appendix](baseline/documents/Heat_Commons_Assumptions_Sources.pdf) identifies the challenge and engineering sources. The app uses [Three.js](https://threejs.org/), with its [MIT license retained](web/vendor/THREE-LICENSE.txt); the standalone presentation includes the same notice. Technical foundations include [scikit-learn](https://scikit-learn.org/stable/) and [SciPy / HiGHS](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.milp.html).
