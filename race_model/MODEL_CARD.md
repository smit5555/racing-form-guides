# RACE-P1 development model

Purpose: a simple explainable challenger for winner identification, not an executable betting system.

The model uses a conditional softmax likelihood for a single winner within each full race field, with an L2 penalty. A runner's probability is exp(utility) divided by the sum of exp(utility) across that race. Shared race constants cancel; features are centred within each field. Scaling is learned on August only and reused unchanged on September. The optimiser is deterministic, starts at zero, checks a numerical gradient in tests and uses decreasing-loss line search.

Nine values and nine explicit missingness indicators are used. Values are recent finish percentile, recent beaten-margin score, distance evidence, going evidence, log days since last completed race, previous minus current weight, published handicap rating, a comparable BM race-number step and barrier divided by the maximum declared active barrier. The four form factors reuse the existing audited V2 feature extractor. Benchmark numbers alone are an incomplete proxy for race strength; no universal class rating is claimed. Missing numeric features are set to zero accompanied by their missing flag.

Historical input excludes races with any active runner not settled, any original eligibility failure, ambiguous identity or multiple/no winners. Confirmed late withdrawals are not removed opportunistically using hindsight to reconstruct a smaller training field: their entire race is omitted in this initial experiment. Original scratches already shown in saved guides remain excluded. This restricts the population and can introduce selection effects; it is not complete Australian racing coverage.

Only x is used as a predictor. win is a supervised target, V2 rank is a comparator and runner identity/date are provenance. No current SP, finish, result text, realised P/L or market-favourite flags appear in x. Prior-start historical evidence is used, subject to the original same-day/future-form and date checks.

Frozen split: August 2026 trains; September 2026 provides a later development diagnostic. Both months were previously reviewed, and the feature design followed that review. September is not an untouched validation period even though it is not used in fitting, scaling, coefficient selection or regularisation search. The fixed L2 strength is 0.1; there is no search for profitable thresholds or weights.

Accuracy is top-pick winners divided by races on exactly the same fields for the model and original V2 rank one. Race log loss is the mean negative log probability of the verified winner; multiclass Brier is mean sum across runners of squared probability error. Lower is better for both losses. Calibration bins show mean predicted chance versus observed win rate, with counts. The model is not post-hoc calibrated and its estimates should not be described as proven reliable probabilities. There is no SP/P&L output or live price threshold in this stage.

The model is saved and hashed before September evaluation. No online requests are made. Existing guides, original locks and settlement archives are unmodified. Outputs must never be presented as predictions genuinely recorded before these historical races.

Method references: [statsmodels conditional logistic regression](https://www.statsmodels.org/stable/generated/statsmodels.discrete.conditional_models.ConditionalLogit.html), [scikit-learn probability calibration and proper scoring rules](https://scikit-learn.org/stable/modules/calibration.html), [scikit-learn data leakage pitfalls](https://scikit-learn.org/stable/common_pitfalls.html).

The implementation is a small NumPy conditional-softmax fit rather than a call to statsmodels; statsmodels is cited for the model family, not as an implementation dependency. No AI service is called. GitHub execution remains subject to the account's normal usage limits.
