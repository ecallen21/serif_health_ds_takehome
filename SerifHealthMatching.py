"""
Serif Health Take Home Assessment

Match TIC <-> HPT on 5 variables, with payer aliasing, a rate comparison, and a
full audit output that keeps matched AND unmatched rows from both files.

The five matching rules (see README for further information):
  1. payer (TIC) ~ payer_name (HPT)      -> fuzzy, drives the confidence score, using payer alias to account for UHC
  2. network_name ~ plan_name            -> filter: keep only HPT rows with "PPO"
  3. code_type ~ code_type               -> exact, after dropping HPT "local" rows
  4. code (TIC) ~ raw_code (HPT)          -> extract code token from messy raw_code, removing the text from the column
  5. cms_baseline_schedule ~ setting      -> conditional substring rule (a gate); see README for definition
"""
import os, re
import pandas as pd
from rapidfuzz import fuzz, utils

# ============================ configuration ================================
PAYER_SCORER    = fuzz.WRatio      # rule 1: scorer best for payer-name variants
PAYER_THRESHOLD = 0.80            # rule 1: min payer similarity for acceptance
PPO_KEYWORD     = "PPO"            # rule 2: substring required in HPT plan_name
DROP_CODE_TYPE  = "local"          # rule 3: HPT code_type value to discard
TIC_RATE_COL    = "rate"          # TIC rate used for calculation
HPT_RATE_COL    = "standard_charge_gross"  # HPT rate used for calculation

# Columns from each file
TIC_COLUMNS = {
    "payer":                 "payer",
    "network_name":          "network_name",
    "network_id":            "network_id",
    "network_year_month":    "network_year_month",
    "network_region":        "network_region",
    "code":                  "code",
    "code_type":             "code_type",
    "ein":                   "ein",
    "taxonomy_filtered_npi_list": "taxonomy_filtered_npi_list",
    "modifier_list":         "modifier_list",
    "billing_class":         "billing_class",
    "place_of_service_list": "place_of_service_list",
    "negotiation_type":      "negotiation_type",
    "arrangement":           "arrangement",
    "rate":                  "rate",
    "cms_baseline_schedule": "cms_baseline_schedule",
    "cms_baseline_rate":     "cms_baseline_rate",

}
HPT_COLUMNS = {
    "source_file_name":         "source_file_name",
    "hospital_id":              "hospital_id",
    "hospital_name":            "hospital_name",
    "last_updated_on":          "last_updated_on",
    "hospital_state":           "hospital_state",
    "license_number":           "license_number",
    "payer_name":               "payer_name",
    "plan_name":                "plan_name",
    "code_type":                "code_type",
    "raw_code":                 "raw_code",
    "description":              "description",
    "setting":                  "setting",
    "modifiers":                "modifiers",
    "standard_charge_gross":    "standard_charge_gross",
    "standard_charge_discounted_cash": "standard_charge_discounted_cash",
    "standard_charge_negotiated_dollar": "standard_charge_negotiated_dollar",
    "standard_charge_negotiated_percentage": "standard_charge_negotiated_percentage",
    "standard_charge_min":      "standard_charge_min",
    "standard_charge_max":      "standard_charge_max",
    "standard_charge_methodology": "standard_charge_methodology",
    "additional_payer_notes":   "additional_payer_notes",
    "additional_generic_notes": "additional_generic_notes",
}

# Payer Aliases
PAYER_ALIASES = [
    (["uhc", "unitedhealth", "united health", "united", "optum", "unitedhealthcare"], "united healthcare"),
    (["aetna"], "aetna"),
    (["cigna", "cigna-corporation"], "cigna"),
]
# Setting rule (rule 5): HPT setting -> allowed substrings in TIC cms_baseline_schedule
SETTING_RULES = {
    "both":       ["_facility", "_nonfacility", "OPPS"],
    "inpatient":  ["IPPS"],
    "outpatient": ["_facility", "OPPS"],
}

# ================================ helpers ==================================
def norm(s):
    # Make everything lowercase
    return str(s).strip().lower()

def canon_payer(name):
    #Rule 1 - Payer Aliases
    s = norm(name)
    for keys, canonical in PAYER_ALIASES:
        if any(k in s for k in keys):
            return canonical
    return s

def payer_sim(a, b):
    #Rule 1 scoring
    return PAYER_SCORER(canon_payer(a), canon_payer(b), processor=utils.default_process) / 100

def hpt_status(row):
    #Rules 2 and 3
    if norm(row["code_type"]) == DROP_CODE_TYPE:          return "code_type_local"
    if PPO_KEYWORD.lower() not in norm(row["plan_name"]):  return "no_ppo"
    return "eligible"

def extract_codes(raw, pattern=None):
    #Rule 4
    s = str(raw).upper()
    toks = re.findall(pattern, s) if pattern else re.split(r"[^A-Za-z0-9]+", s)
    return [t for t in toks if t]

def setting_ok(setting, cms_sched):
    #Rule 5
    allowed = SETTING_RULES.get(norm(setting), [])
    return any(tok in norm(cms_sched) for tok in allowed)

# =============================== pipeline ==================================
def run(tic, hpt, code_pattern=None):
    tic_in = tic.copy(); hpt_in = hpt.copy()
    tic = tic.copy().rename(columns=TIC_COLUMNS)
    hpt = hpt.copy().rename(columns=HPT_COLUMNS)

    #Give every row a stable id so it can be tracked through joins
    tic["_tic_id"] = range(len(tic));
    hpt["_hpt_id"] = range(len(hpt))
    hpt["_excl"] = hpt.apply(hpt_status, axis=1)  # tag every HPT row

    #Pre-filter HPT to eligible rows (rules 2 & 3)
    elig = hpt[hpt["_excl"] == "eligible"].copy()

    #Build normalized join keys (rules 3 & 4)
    tic["_ct"] = tic["code_type"].map(norm)
    elig["_ct"] = elig["code_type"].map(norm)
    tic["_code"] = tic["code"].astype(str).str.upper().str.strip()
    elig["_codes"] = elig["raw_code"].apply(lambda r: extract_codes(r, code_pattern))
    elig_x = elig.explode("_codes").rename(columns={"_codes": "_code"})

    # Merge on code_type + code
    cand = tic.merge(elig_x, on=["_ct", "_code"], suffixes=("_tic", "_hpt"))

    #Apply remaining gates to the candidates
    cand = cand[cand.apply(lambda r: setting_ok(r["setting"], r["cms_baseline_schedule"]), axis=1)]
    cand["payer_score"] = cand.apply(lambda r: payer_sim(r["payer"], r["payer_name"]), axis=1)
    cand = cand[cand["payer_score"] >= PAYER_THRESHOLD].copy()
    cand = cand.drop_duplicates(subset=["_tic_id", "_hpt_id"])

    #Scores + rate comparison on surviving matches
    cand["code_score"] = 1.0
    cand["confidence"] = (2 * cand["payer_score"] + cand["code_score"]) / 3
    cand["rate_tic"] = cand[TIC_RATE_COL]
    cand["rate_hpt"] = cand[HPT_RATE_COL]
    cand["rate_diff"] = cand["rate_tic"] - cand["rate_hpt"]
    cand["rate_ratio"] = cand["rate_tic"] / cand["rate_hpt"]

    # Assemble full audit output (matched + both sides unmatched), keep all original columns + new
    tic_o = tic_in.reset_index(drop=True).add_prefix("tic_")
    hpt_o = hpt_in.reset_index(drop=True).add_prefix("hpt_")

    computed = ["confidence", "payer_score", "code_score",
                "rate_tic", "rate_hpt", "rate_diff", "rate_ratio"]

    # Matched pairs
    m = pd.concat([
        cand[computed].reset_index(drop=True),
        tic_o.loc[cand["_tic_id"]].reset_index(drop=True),
        hpt_o.loc[cand["_hpt_id"]].reset_index(drop=True),
    ], axis=1)
    m.insert(0, "status", "matched")
    m["hpt_exclusion"] = "eligible"

    #TIC rows that never matched: all original TIC cols, HPT side blank
    tu_ids = [i for i in tic["_tic_id"] if i not in set(cand["_tic_id"])]
    t = tic_o.loc[tu_ids].reset_index(drop=True)
    t.insert(0, "status", "tic_unmatched")
    t["rate_tic"] = tic.set_index("_tic_id").loc[tu_ids, TIC_RATE_COL].to_numpy()

    #HPT rows that never matched: all original HPT cols + the exclusion reason
    hu_ids = [i for i in hpt["_hpt_id"] if i not in set(cand["_hpt_id"])]
    h = hpt_o.loc[hu_ids].reset_index(drop=True)
    h.insert(0, "status", "hpt_unmatched")
    h["rate_hpt"] = hpt.set_index("_hpt_id").loc[hu_ids, HPT_RATE_COL].to_numpy()
    h["hpt_exclusion"] = hpt.set_index("_hpt_id").loc[hu_ids, "_excl"].to_numpy()

    # Stack
    out = pd.concat([m, t, h], ignore_index=True)

    # Altogether
    lead = ["status"] + computed + ["hpt_exclusion"]
    tic_cols = [c for c in out.columns if c.startswith("tic_")]
    hpt_cols = [c for c in out.columns if c.startswith("hpt_") and c != "hpt_exclusion"]
    return out[lead + tic_cols + hpt_cols]


# ================================== run ====================================
if __name__ == "__main__":
    TIC_PATH = "C:\\Users\\efcal\\Downloads\\tic_extract_20250213.csv"
    HPT_PATH = "C:\\Users\\efcal\\Downloads\\hpt_extract_20250213.csv"
    OUTPUT_PATH = "C:\\Users\\efcal\\Downloads\\serif_health_output.csv"

    tic = pd.read_csv(TIC_PATH)
    hpt = pd.read_csv(HPT_PATH)

    res = run(tic, hpt)
    res.to_csv(OUTPUT_PATH, index=False)
    print(f"Wrote {len(res)} rows to {OUTPUT_PATH}")
    print(res["status"].value_counts().to_string())