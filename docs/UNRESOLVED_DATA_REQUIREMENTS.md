# Unresolved Data Requirements (prioritised)

Nothing below was available in this environment. Until items P1-P3 are
obtained, **no empirical conclusion about any strategy is possible**.

| Priority | Data | Markets | Unlocks | Notes / likely source (licensing required) |
|---|---|---|---|---|
| P1 | Daily bars with the closing-auction decomposition: last continuous price + timestamp, closing auction price, matched volume, close method, open-auction volume, suspensions | all | S01, S02, S03, S06 and all diagnostics | SETSMART / SET Market Data; SGX market data; HKEX Data Marketplace. Must include delisted securities (survivorship). |
| P2 | Actual auction match timestamps and real-time receipt times of auction results | all | Causal timing of S03/S04/S07; validation of the random-close window history | Exchange feed archives (SET ITCH/feed, SGX, HKEX OMD-C). EOD files alone make S07 untestable. |
| P3 | Stock-borrow availability and fees (point in time) + exchange short-sale eligibility lists with dates | all | Short legs of S01/S02/S05/S06/S07 | Broker SBL data, SET short-sale eligible list, HKEX designated securities list history, SGX lending pool |
| P4 | Historical indicative equilibrium price / volume / imbalance during closing auctions, with timestamps | all | S04 entirely | Rarely archived; requires tick-level feed history. S04 is **unsupported** on real data until then. |
| P5 | SGX Trade-at-Close volumes and trade records, separated from closing-auction matches; TAC introduction date | SGX | S07 | SGX trade-level data with phase flags |
| P6 | Point-in-time index constituents, weights and review announcements (announce time, effective date) for SET50/SET100, STI, HSI/HSCEI, FTSE/MSCI where relevant | all | S05, S08 index construction | Index providers (SET, FTSE Russell, Hang Seng Indexes, MSCI) - licensed |
| P7 | Index futures intraday prices aligned to the cash closing auction (SET50 futures, FTSE STI futures, HSI futures) | all | S08 | TFEX, SGX-DT, HKFE tick data |
| P8 | Corporate actions with announcement timestamps | all | PIT adjustment checks | Exchange announcements / vendor CA feeds |
| P9 | Official holiday and half-day calendars 2018-2026 | all | Calendar validation | Exchange circulars |
| P10 | Historical order-book depth | all | Replace scenario-based fills with book-based fill estimates | Full-depth feed archives |
| P11 | Official SGX trading rules (phases, TAC, fees) - every SGX rule is currently `secondary_unverified` because sgx.com and rulebook.sgx.com could not be read from this environment | SGX | Trust in SGX simulations | SGX-ST Trading Rules & circulars |
| P12 | Historical rule-change dates: SET random-close introduction, SET short-sale rule changes (2024), HKEX CAS scope expansions after 2016, HKEX tick-size reductions (2025) | SET, HKEX | Removes `historically_assumed` flags | Exchange circulars / notices |
| P13 | Commission and fee schedules actually paid (negotiated) | all | Cost calibration | Broker contracts |
