"""ML training library shared by training, backtests, and live inference (section 10, ADR-0036).

Modules: ``config`` (TrainingConfig defaults and hash), ``featuresets``, ``dataset`` (snapshots),
``preprocess`` (fold-fitted pipeline), ``models`` (model zoo and hyperparameter schemas), ``metrics``,
``rules`` (primary rules for the ML filter families), ``train`` (fold loop, evaluation), ``diagnostics``
(``DiagnosticsReport``), ``bundle`` (persisted pipeline + model for inference), ``synthetic``.
"""
