"""Generator for s3-data/pricing/*-pricing.json (indicative demo rates).

Run from the repo root: python3 tools/pricing/build_rates.py
The rate tables below are the source; the JSON files are the output and are
committed. Worked examples are computed by lambda/pricing itself, so the price
card in Data Explorer always agrees with what the pricing tool returns.

Calibration:
- Critical illness, health and disability tables are copied from the
  "Pricing" sections of the Awesome product sheets, so the pricing tool
  agrees with the sheets.
- Awesome Term Life is set so that $500,000 over 20 years at age 35 (non-smoker,
  male) is $420 a year, as the competitive documents quote.
- The other products have no published prices, so their rates are invented
  and sized to be of the same order as the demo customers' existing policies.
"""
import json
import pathlib

OUT = pathlib.Path("s3-data/pricing")
OUT.mkdir(parents=True, exist_ok=True)
EFFECTIVE = "2026-10-01"

FREQ_LOADING = {"annual": 1.0, "semi_annual": 1.02, "quarterly": 1.03, "monthly": 1.05}
FREQ_INPUT = {
    "type": "choice",
    "choices": ["annual", "semi_annual", "quarterly", "monthly"],
    "default": "annual",
    "description": "How often the premium is paid. Paying more often adds a small instalment loading.",
}

LIFE_BANDS = [(18, 25), (26, 30), (31, 35), (36, 40), (41, 45), (46, 50), (51, 55), (56, 60), (61, 65), (66, 70)]


def bands(pairs):
    return [{"min": a, "max": b} for a, b in pairs]


def inp_age(lo, hi, desc="Age last birthday"):
    return {"type": "number", "min": lo, "max": hi, "required": True, "source": "profile", "description": desc}


INP_SMOKER = {"type": "boolean", "required": True, "source": "profile",
              "description": "Whether the person smokes or has used tobacco in the last 12 months."}
INP_GENDER = {"type": "choice", "choices": ["male", "female"], "required": True, "source": "profile",
              "description": "Used to apply the gender factor. Taken from the customer's profile; if it is missing (for example a new prospect), ask the advisor."}
# Health, critical illness and disability: the gender input is described as working with the age band.
INP_GENDER_BANDED = {**INP_GENDER, "description": "Used with the age band to apply the gender factor. Taken from the customer's profile; if it is missing (for example a new prospect), ask the advisor."}
# Female multiplier per age band. Male is the reference (1.0) and equals the published sheet rate.
# These are illustrative placeholders until the pricing factors are confirmed.
def gender_band_factor(age_pairs, mults):
    assert len(age_pairs) == len(mults)
    out = []
    for i, ((lo, hi), m) in enumerate(zip(age_pairs, mults)):
        out.append({**({"min": lo} if i else {}), "max": hi, "mult": m})
    return {"input": "age", "label": "Gender factor (female, by age band; male = 1.0)", "when": {"gender": "female"}, "bands": out}
GENDER_NOTE = ("Table values are the published rates and are the male reference. A female applicant's premium is the table value "
               "times the female factor for her age band (Gender factor). The gender factors are illustrative placeholders until the pricing factors are confirmed.")



# Each rate file is named after the product's portfolio file.
SLUGS = {
    "term_life": "awesome-term-life", "whole_life": "awesome-whole-life",
    "universal_life": "awesome-universal-life", "variable_life": "awesome-variable-life",
    "critical_illness": "awesome-critical-illness", "health": "awesome-health",
    "home": "awesome-home", "auto": "awesome-motor",
    "travel": "awesome-travel", "disability": "awesome-disability",
}
def rate_file(product_type):
    return OUT / f"{SLUGS[product_type]}-pricing.json"
def write(name, data):
    data.setdefault("currency", "USD")
    data.setdefault("effective_date", EFFECTIVE)
    data.setdefault("frequency_loading", FREQ_LOADING)
    data["indicative"] = True
    rate_file(name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print("wrote", name)


# ---------------------------------------------------------------- life family
def life(product_type, name, rates, sum_lo, sum_hi, extra_inputs, extra_factors, notes, extra_required=None):
    inputs = {
        "age": inp_age(18, 70),
        "gender": INP_GENDER,
        "smoker": INP_SMOKER,
        "sum_assured": {"type": "number", "min": sum_lo, "max": sum_hi, "required": True,
                        "description": "Death benefit in USD."},
    }
    inputs.update(extra_inputs)
    inputs["premium_frequency"] = FREQ_INPUT
    factors = [
        {"input": "gender", "label": "Gender", "map": {"male": 1.0, "female": 0.88}},
        {"input": "smoker", "label": "Smoker", "map": {"true": 1.0 if False else 1.9, "false": 1.0}},
    ] + extra_factors
    write(product_type, {
        "product_type": product_type,
        "product_name": name,
        "premium_basis": "per year",
        "notes": notes,
        "inputs": inputs,
        "base": {"type": "age_rate", "amount_input": "sum_assured", "unit": 1000,
                 "age_bands": bands(LIFE_BANDS), "rates": rates,
                 "label": "Annual rate per $1,000 of cover, male non-smoker, at the reference choices"},
        "factors": factors,
    })


life("term_life", "Awesome Term Life",
     [0.52, 0.60, 0.84, 1.30, 2.10, 3.20, 4.90, 7.60, 12.0, 19.0], 250000, 2000000,
     {"term_years": {"type": "choice", "choices": ["10", "15", "20", "25", "30"], "required": True,
                     "description": "Length of the term in years."},
      "premium_structure": {"type": "choice", "choices": ["level", "return_of_premium"], "default": "level",
                            "description": "Level premiums, or return of premium (all premiums refunded at the end of the term if no claim)."}},
     [{"input": "term_years", "label": "Term", "map": {"10": 0.80, "15": 0.90, "20": 1.0, "25": 1.15, "30": 1.30}},
      {"input": "premium_structure", "label": "Premium structure", "map": {"level": 1.0, "return_of_premium": 1.9}}],
     ["Reference point: $500,000 over 20 years at age 35, male non-smoker = $420 a year."])

life("whole_life", "Awesome Whole Life",
     [4.8, 5.8, 7.0, 8.5, 10.5, 13.0, 16.0, 20.0, 25.0, 31.0], 100000, 2500000,
     {"premium_schedule": {"type": "choice", "choices": ["lifetime", "10_pay", "20_pay", "paid_up_65"], "required": True,
                           "description": "Lifetime pay, paid up after 10 or 20 years, or paid up at 65."}},
     [{"input": "premium_schedule", "label": "Premium schedule",
       "map": {"lifetime": 1.0, "20_pay": 1.38, "10_pay": 2.1, "paid_up_65": 1.55}}],
     ["Rates are for lifetime pay; shorter payment schedules cost more each year.",
      "Paid-up at 65 is not offered to applicants aged 60 or over."])

life("universal_life", "Awesome Universal Life",
     [2.2, 2.6, 3.2, 4.0, 5.0, 6.3, 8.0, 10.5, 14.0, 19.0], 250000, 5000000,
     {"death_benefit_option": {"type": "choice", "choices": ["option_a", "option_b"], "required": True,
                               "description": "option_a is a level face amount; option_b is face amount plus cash value."},
      "premium_level": {"type": "choice", "choices": ["minimum", "target", "overfund"], "default": "target",
                        "description": "Minimum to keep the policy in force, the target premium, or overfunding to build cash value faster."}},
     [{"input": "death_benefit_option", "label": "Death benefit option", "map": {"option_a": 1.0, "option_b": 1.18}},
      {"input": "premium_level", "label": "Premium level", "map": {"minimum": 0.6, "target": 1.0, "overfund": 1.6}}],
     ["Rates are for the target premium. Cash value depends on the declared interest rate and is not projected here."])

life("variable_life", "Awesome Variable Life",
     [3.0, 3.6, 4.4, 5.4, 6.8, 8.6, 11.0, 14.5, 19.0, 25.0], 500000, 10000000,
     {"death_benefit_option": {"type": "choice", "choices": ["option_a", "option_b", "option_c"], "required": True,
                               "description": "option_a level face amount; option_b face amount plus sub-account value; option_c face amount plus return of premiums."},
      "premium_level": {"type": "choice", "choices": ["minimum", "target", "overfund"], "default": "target",
                        "description": "Minimum to keep the policy in force, the target premium, or overfunding."}},
     [{"input": "death_benefit_option", "label": "Death benefit option",
       "map": {"option_a": 1.0, "option_b": 1.15, "option_c": 1.12}},
      {"input": "premium_level", "label": "Premium level", "map": {"minimum": 0.65, "target": 1.0, "overfund": 1.5}}],
     ["Rates are for the target premium. Sub-account values depend on markets and may fall; more premium may be needed to keep the policy in force."])

# ------------------------------------------------------------ critical illness
write("critical_illness", {
    "product_type": "critical_illness",
    "product_name": "Awesome Critical Illness",
    "premium_basis": "per year",
    "notes": [GENDER_NOTE + " Basis: non-smoker with $100,000 cover; other cover amounts scale in proportion.",
              "Ages outside 25 to 65 have no published rate."],
    "inputs": {
        "age": inp_age(25, 65),
        "gender": INP_GENDER_BANDED,
        "smoker": INP_SMOKER,
        "lump_sum": {"type": "choice", "choices": ["25000", "50000", "100000", "250000", "500000"], "required": True,
                     "description": "Lump-sum amount per stage, in USD."},
        "illness_tier": {"type": "choice", "choices": ["basic", "standard", "comprehensive"], "required": True,
                         "description": "basic: 3 conditions; standard: 8 conditions; comprehensive: 20+ conditions."},
        "cover_term": {"type": "choice", "choices": ["10", "15", "20", "30", "to_65", "to_75"], "default": "20",
                       "description": "Coverage term in years, or to age 65 or 75."},
        "premium_structure": {"type": "choice", "choices": ["level", "stepped"], "default": "level",
                              "description": "Level premiums fixed for the term, or stepped premiums that start lower and rise with age."},
        "return_of_premium": {"type": "boolean", "default": False,
                              "description": "Get all premiums back at the end of the term if no claim is made (adds about 40%)."},
        "premium_frequency": FREQ_INPUT,
    },
    "base": {"type": "age_column", "amount_input": "lump_sum", "per": 100000, "column_input": "illness_tier",
             "age_bands": bands([(25, 30), (31, 35), (36, 45), (46, 55), (56, 65)]),
             "columns": {
                 "basic": [240, 360, 600, 1080, 1680],
                 "standard": [420, 600, 1020, 1800, 2760],
                 "comprehensive": [660, 960, 1620, 2880, 4320]},
             "label": "Annual premium for $100,000 of cover, non-smoker (product sheet)"},
    "factors": [
        {"input": "smoker", "label": "Smoker", "map": {"true": 1.6, "false": 1.0}},
        gender_band_factor([(25, 30), (31, 35), (36, 45), (46, 55), (56, 65)], [1.15, 1.12, 1.08, 1.00, 0.92]),
        {"input": "cover_term", "label": "Coverage term",
         "map": {"10": 0.9, "15": 0.95, "20": 1.0, "30": 1.1, "to_65": 1.0, "to_75": 1.15}},
        {"input": "premium_structure", "label": "Premium structure", "map": {"level": 1.0, "stepped": 0.85}},
        {"input": "return_of_premium", "label": "Return of premium", "map": {"true": 1.4, "false": 1.0}},
    ],
})

# --------------------------------------------------------------------- health
write("health", {
    "product_type": "health",
    "product_name": "Awesome Health",
    "premium_basis": "per year",
    "notes": [GENDER_NOTE + " Basis: individual non-smoker (product sheet), $50,000 annual limit, $500 deductible and standard network.",
              "Rates exclude multi-policy and no-claim discounts, which are decided at sale.",
              "Ages outside 18 to 65 have no published rate."],
    "inputs": {
        "age": inp_age(18, 65),
        "gender": INP_GENDER_BANDED,
        "smoker": INP_SMOKER,
        "tier": {"type": "choice", "choices": ["essential", "standard", "premium"], "required": True,
                 "description": "essential: hospital and emergency; standard: adds outpatient and specialists; premium: adds dental, vision and mental health."},
        "annual_limit": {"type": "choice", "choices": ["10000", "25000", "50000", "100000", "250000"], "default": "50000",
                         "description": "Annual maximum benefit in USD."},
        "deductible": {"type": "choice", "choices": ["0", "250", "500", "1000", "2500"], "default": "500",
                       "description": "Annual deductible in USD."},
        "network": {"type": "choice", "choices": ["standard", "extended", "open_access"], "default": "standard",
                    "description": "Hospital network."},
        "reimbursement": {"type": "choice", "choices": ["direct_billing", "reimbursement"], "default": "direct_billing",
                          "description": "Insurer pays the provider directly, or the policyholder claims back."},
        "international": {"type": "boolean", "default": False, "description": "International coverage add-on."},
        "chronic": {"type": "boolean", "default": False, "description": "Chronic condition management add-on."},
        "maternity": {"type": "boolean", "default": False, "description": "Maternity add-on (12-month waiting period)."},
        "hospital_cash": {"type": "boolean", "default": False, "description": "Hospital cash benefit add-on."},
        "hospital_cash_daily": {"type": "number", "min": 100, "max": 500, "default": 100,
                                "description": "Hospital cash per day in USD, 100 to 500. Only used when hospital_cash is true."},
        "premium_frequency": FREQ_INPUT,
    },
    "base": {"type": "age_column", "amount_input": None, "column_input": "tier",
             "age_bands": bands([(18, 25), (26, 35), (36, 45), (46, 55), (56, 65)]),
             "columns": {
                 "essential": [780, 960, 1440, 2160, 3240],
                 "standard": [1200, 1560, 2280, 3480, 5160],
                 "premium": [1800, 2400, 3600, 5400, 7800]},
             "label": "Annual premium, individual non-smoker (product sheet)"},
    "factors": [
        {"input": "smoker", "label": "Smoker", "map": {"true": 1.15, "false": 1.0}},
        gender_band_factor([(18, 25), (26, 35), (36, 45), (46, 55), (56, 65)], [1.10, 1.15, 1.08, 1.00, 0.95]),
        {"input": "annual_limit", "label": "Annual limit", "map": {"10000": 0.8, "25000": 0.9, "50000": 1.0, "100000": 1.15, "250000": 1.35}},
        {"input": "deductible", "label": "Deductible", "map": {"0": 1.15, "250": 1.07, "500": 1.0, "1000": 0.92, "2500": 0.8}},
        {"input": "network", "label": "Hospital network", "map": {"standard": 1.0, "extended": 1.12, "open_access": 1.25}},
        {"input": "reimbursement", "label": "Reimbursement model", "map": {"direct_billing": 1.0, "reimbursement": 0.95}},
    ],
    "addons": [
        {"input": "international", "label": "International coverage", "pct": 0.18},
        {"input": "chronic", "label": "Chronic condition management", "pct": 0.10},
        {"input": "maternity", "label": "Maternity", "flat": 1500},
        {"input": "hospital_cash", "label": "Hospital cash benefit", "flat_per_unit": {"input": "hospital_cash_daily", "per": 100, "amount": 90}},
    ],
})

# ----------------------------------------------------------------- disability
write("disability", {
    "product_type": "disability",
    "product_name": "Awesome Disability",
    "premium_basis": "per year",
    "notes": [GENDER_NOTE + " Basis: Own Occupation, Class 1 (office professional); benefits between the published amounts are interpolated, and $2,000 scales from the $3,000 row.",
              "The monthly benefit is normally capped at 60 to 70% of pre-disability income.",
              "Ages outside 25 to 60 have no published rate."],
    "inputs": {
        "age": inp_age(25, 60),
        "gender": INP_GENDER_BANDED,
        "smoker": INP_SMOKER,
        "monthly_benefit": {"type": "number", "min": 2000, "max": 15000, "required": True, "description": "Monthly benefit in USD."},
        "benefit_period": {"type": "choice", "choices": ["2_years", "5_years", "10_years", "to_retirement"], "default": "to_retirement",
                           "description": "How long benefits are paid during one claim."},
        "waiting_period": {"type": "choice", "choices": ["30", "60", "90", "180", "365"], "default": "90",
                           "description": "Days before benefits begin."},
        "occupation_class": {"type": "choice", "choices": ["class_1", "class_2", "class_3", "class_4", "class_5"], "default": "class_1",
                             "description": "Class 1 office professional to Class 5 heavy manual."},
        "definition": {"type": "choice", "choices": ["own_occupation", "any_occupation", "hybrid"], "default": "own_occupation",
                       "description": "Own occupation, any occupation, or own occupation for a set period then any occupation."},
        "coverage_type": {"type": "choice", "choices": ["short_term", "long_term", "combined"], "default": "long_term",
                          "description": "Short-term (up to 2 years), long-term, or combined."},
        "cola": {"type": "boolean", "default": False, "description": "Cost of living adjustment rider."},
        "future_purchase": {"type": "boolean", "default": False, "description": "Future purchase option rider."},
        "partial_disability": {"type": "boolean", "default": False, "description": "Partial disability benefit rider."},
        "premium_frequency": FREQ_INPUT,
    },
    "base": {"type": "age_amount", "amount_input": "monthly_benefit",
             "age_bands": bands([(25, 30), (31, 40), (41, 50), (51, 60)]),
             "rows": {"3000": [420, 660, 1020, 1680], "5000": [660, 1020, 1620, 2700], "8000": [1020, 1560, 2520, 4200],
                      "10000": [1260, 1920, 3120, 5160], "15000": [1860, 2820, 4560, 7560]},
             "label": "Annual premium, Own Occupation, Class 1, at a 90-day wait, to retirement (product sheet)"},
    "factors": [
        {"input": "smoker", "label": "Smoker", "map": {"true": 1.25, "false": 1.0}},
        gender_band_factor([(25, 30), (31, 40), (41, 50), (51, 60)], [1.10, 1.20, 1.25, 1.15]),
        {"input": "occupation_class", "label": "Occupation class", "map": {"class_1": 1.0, "class_2": 1.15, "class_3": 1.35, "class_4": 1.6, "class_5": 1.9}},
        {"input": "definition", "label": "Definition of disability", "map": {"own_occupation": 1.0, "any_occupation": 0.7, "hybrid": 0.85}},
        {"input": "waiting_period", "label": "Waiting period", "map": {"30": 1.25, "60": 1.1, "90": 1.0, "180": 0.85, "365": 0.7}},
        {"input": "benefit_period", "label": "Benefit period", "map": {"2_years": 0.55, "5_years": 0.75, "10_years": 0.9, "to_retirement": 1.0}},
        {"input": "coverage_type", "label": "Coverage type", "map": {"short_term": 0.6, "long_term": 1.0, "combined": 1.35}},
    ],
    "addons": [
        {"input": "cola", "label": "Cost of living adjustment", "pct": 0.15},
        {"input": "future_purchase", "label": "Future purchase option", "pct": 0.10},
        {"input": "partial_disability", "label": "Partial disability benefit", "pct": 0.08},
    ],
})

# ----------------------------------------------------------------------- home
write("home", {
    "product_type": "home",
    "product_name": "Awesome Home Insurance",
    "premium_basis": "per year",
    "notes": ["Invented demo rates sized to the demo customers' existing home policies (about $2.40 per $1,000 of rebuilding cost for a single-family home)."],
    "inputs": {
        "dwelling_value": {"type": "number", "min": 100000, "max": 3000000, "required": True,
                           "description": "Estimated rebuilding cost of the home in USD."},
        "property_type": {"type": "choice", "choices": ["single_family", "condominium", "townhouse", "rental", "vacation"], "required": True,
                          "description": "Type of property."},
        "deductible": {"type": "choice", "choices": ["500", "1000", "2500", "5000"], "default": "1000", "description": "Deductible in USD."},
        "contents_percent": {"type": "choice", "choices": ["50", "70", "100"], "default": "70", "description": "Personal property cover as a % of the dwelling amount."},
        "liability": {"type": "choice", "choices": ["100000", "300000", "500000", "1000000"], "default": "300000", "description": "Personal liability limit in USD."},
        "living_expenses_months": {"type": "choice", "choices": ["12", "24", "36"], "default": "12", "description": "Months of additional living expenses covered."},
        "claims_last_5_years": {"type": "number", "min": 0, "max": 10, "default": 0, "description": "Home claims in the last 5 years."},
        "flood": {"type": "boolean", "default": False, "description": "Flood protection."},
        "home_business": {"type": "boolean", "default": False, "description": "Home business coverage."},
        "premium_frequency": FREQ_INPUT,
    },
    "base": {"type": "rate_by_choice", "amount_input": "dwelling_value", "unit": 1000, "choice_input": "property_type",
             "rates": {"single_family": 2.4, "condominium": 1.8, "townhouse": 2.3, "rental": 3.2, "vacation": 3.6},
             "label": "Annual rate per $1,000 of rebuilding cost, at the default choices"},
    "factors": [
        {"input": "deductible", "label": "Deductible", "map": {"500": 1.12, "1000": 1.0, "2500": 0.88, "5000": 0.78}},
        {"input": "contents_percent", "label": "Personal property cover", "map": {"50": 0.95, "70": 1.0, "100": 1.12}},
        {"input": "liability", "label": "Liability limit", "map": {"100000": 0.95, "300000": 1.0, "500000": 1.08, "1000000": 1.2}},
        {"input": "living_expenses_months", "label": "Living expenses", "map": {"12": 1.0, "24": 1.05, "36": 1.1}},
        {"input": "claims_last_5_years", "label": "Claims history", "map": {"0": 1.0, "1": 1.15, "2+": 1.35}},
    ],
    "addons": [
        {"input": "flood", "label": "Flood protection", "pct": 0.35},
        {"input": "home_business", "label": "Home business coverage", "pct": 0.12},
    ],
})

# ----------------------------------------------------------------------- auto
write("auto", {
    "product_type": "auto",
    "product_name": "Awesome Motor",
    "premium_basis": "per year",
    "notes": ["Invented demo rates sized to the demo customers' existing motor policies ($850 to $1,400 a year)."],
    "inputs": {
        "age": inp_age(18, 90, "Age of the main driver"),
        "cover_type": {"type": "choice", "choices": ["third_party", "third_party_fire_theft", "comprehensive"], "required": True,
                       "description": "Third party only, third party fire and theft, or comprehensive."},
        "vehicle_value": {"type": "number", "min": 1000, "max": 300000, "required": True, "description": "Vehicle value in USD."},
        "deductible": {"type": "choice", "choices": ["250", "500", "1000", "2000"], "default": "500", "description": "Deductible in USD."},
        "liability_limits": {"type": "choice", "choices": ["25k_50k", "50k_100k", "100k_300k", "250k_500k"], "default": "50k_100k",
                             "description": "Bodily injury / property damage limits."},
        "usage": {"type": "choice", "choices": ["personal", "commuting", "business", "commercial"], "default": "personal", "description": "Vehicle usage."},
        "annual_mileage": {"type": "choice", "choices": ["under_5000", "5000_10000", "10000_15000", "over_15000"], "default": "5000_10000",
                           "description": "Estimated annual mileage."},
        "driver_cover": {"type": "choice", "choices": ["named_drivers", "any_driver"], "default": "named_drivers", "description": "Named drivers only, or any driver over a set age."},
        "accidents_5y": {"type": "number", "min": 0, "max": 10, "default": 0, "description": "At-fault accidents in the last 5 years."},
        "roadside": {"type": "boolean", "default": False, "description": "Roadside assistance."},
        "new_vehicle_replacement": {"type": "boolean", "default": False, "description": "New vehicle replacement (vehicle under 2 years old)."},
        "premium_frequency": FREQ_INPUT,
    },
    "base": {"type": "flat_by_choice", "choice_input": "cover_type",
             "values": {"third_party": 520, "third_party_fire_theft": 760, "comprehensive": 1100},
             "label": "Annual base premium by cover type, at the default choices"},
    "factors": [
        {"input": "vehicle_value", "label": "Vehicle value",
         "bands": [{"max": 14999, "mult": 0.85}, {"min": 15000, "max": 29999, "mult": 1.0},
                   {"min": 30000, "max": 49999, "mult": 1.25}, {"min": 50000, "mult": 1.5}]},
        {"input": "age", "label": "Driver age",
         "bands": [{"max": 24, "mult": 1.6}, {"min": 25, "max": 29, "mult": 1.15},
                   {"min": 30, "max": 64, "mult": 1.0}, {"min": 65, "mult": 1.15}]},
        {"input": "deductible", "label": "Deductible", "map": {"250": 1.12, "500": 1.0, "1000": 0.9, "2000": 0.82}},
        {"input": "liability_limits", "label": "Liability limits", "map": {"25k_50k": 0.9, "50k_100k": 1.0, "100k_300k": 1.12, "250k_500k": 1.3}},
        {"input": "usage", "label": "Usage", "map": {"personal": 1.0, "commuting": 1.1, "business": 1.25, "commercial": 1.5}},
        {"input": "annual_mileage", "label": "Annual mileage", "map": {"under_5000": 0.9, "5000_10000": 1.0, "10000_15000": 1.1, "over_15000": 1.2}},
        {"input": "driver_cover", "label": "Driver cover", "map": {"named_drivers": 1.0, "any_driver": 1.15}},
        {"input": "accidents_5y", "label": "Accident history", "map": {"0": 1.0, "1": 1.25, "2+": 1.6}},
    ],
    "addons": [
        {"input": "roadside", "label": "Roadside assistance", "flat": 60},
        {"input": "new_vehicle_replacement", "label": "New vehicle replacement", "flat": 140},
    ],
})

# --------------------------------------------------------------------- travel
write("travel", {
    "product_type": "travel",
    "product_name": "Awesome Travel",
    "premium_basis": "per trip for a single trip, per year for annual multi-trip",
    "notes": ["Invented demo rates. A single trip is priced per day of travel and per traveller; annual multi-trip is priced per traveller for the year.",
              "Age is the age of the oldest traveller."],
    "inputs": {
        "age": inp_age(0, 90, "Age of the oldest traveller"),
        "duration": {"type": "choice", "choices": ["single_trip", "annual_multi_trip"], "required": True,
                     "description": "Single trip (1 to 90 days) or annual multi-trip."},
        "geographic": {"type": "choice", "choices": ["regional", "continental", "worldwide"], "required": True, "description": "Geographic scope."},
        "trip_days": {"type": "number", "min": 1, "max": 90, "required_when": {"duration": "single_trip"}, "description": "Days of travel for a single trip."},
        "max_trip_days": {"type": "choice", "choices": ["30", "45", "60"], "default": "30", "required_when": {"duration": "annual_multi_trip"},
                          "description": "Longest single trip allowed under the annual policy."},
        "level": {"type": "choice", "choices": ["basic", "standard", "premium", "elite"], "default": "standard", "description": "Coverage level."},
        "medical": {"type": "choice", "choices": ["50000", "100000", "500000", "1000000", "unlimited"], "default": "100000",
                    "description": "Emergency medical cover in USD, or unlimited."},
        "trip_cancellation": {"type": "number", "min": 0, "max": 25000, "default": 0, "description": "Prepaid, non-refundable trip cost to cover, 1,500 to 25,000 (0 for none)."},
        "travellers": {"type": "number", "min": 1, "max": 10, "default": 1, "description": "Number of travellers."},
        "adventure_sports": {"type": "boolean", "default": False, "description": "Adventure sports coverage."},
        "rental_car": {"type": "boolean", "default": False, "description": "Rental car protection."},
    },
    "base": {"type": "trip", "duration_input": "duration", "travellers_input": "travellers",
             "per_day": {"regional": 3.2, "continental": 4.5, "worldwide": 6.5},
             "annual": {"regional": 180, "continental": 260, "worldwide": 380},
             "days_input": "trip_days", "geographic_input": "geographic",
             "label": "Single trip: rate per day per traveller. Annual: rate per traveller per year"},
    "factors": [
        {"input": "level", "label": "Coverage level", "map": {"basic": 0.8, "standard": 1.0, "premium": 1.3, "elite": 1.7}},
        {"input": "medical", "label": "Medical cover", "map": {"50000": 0.8, "100000": 1.0, "500000": 1.25, "1000000": 1.45, "unlimited": 1.8}},
        {"input": "age", "label": "Traveller age",
         "bands": [{"max": 30, "mult": 0.85}, {"min": 31, "max": 60, "mult": 1.0}, {"min": 61, "max": 70, "mult": 1.7},
                   {"min": 71, "max": 80, "mult": 2.6}, {"min": 81, "mult": 4.0}]},
        {"input": "max_trip_days", "label": "Longest trip", "when": {"duration": "annual_multi_trip"},
         "map": {"30": 1.0, "45": 1.2, "60": 1.4}},
    ],
    "addons": [
        {"input": "adventure_sports", "label": "Adventure sports", "pct": 0.25},
        {"input": "rental_car", "label": "Rental car protection", "flat": 40},
        {"input": "trip_cancellation", "label": "Trip cancellation cover", "rate_on_input": 0.045},
    ],
})

# --------------------------------------------------------------- affordability
(OUT / "affordability.json").write_text(json.dumps({
    "rule": "affordability_cap",
    "cap_percent_of_annual_income": 10,
    "description": "Total annual premium across all of a customer's active policies, including other insurers' policies and the proposed one, should not exceed 10% of annual income.",
    "applies_to": "all products",
    "if_income_missing": "Ask the advisor for the customer's annual income. Do not guess it.",
}, indent=2) + "\n")
print("wrote affordability")


# ------------------------------------------------------------ worked examples
# Each example is priced by lambda/pricing/index.py (the same code the
# estimate_premium tool runs) and stored in the rate file for Data Explorer's
# price card. If a rate changes, re-run this script to refresh them.
import importlib.util

_spec = importlib.util.spec_from_file_location("pricing_lambda", pathlib.Path("lambda/pricing/index.py"))
_pricing = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pricing)

EXAMPLES = {
    "term_life": [
        ("Age 35, male, non-smoker, $500,000 over 20 years", {"age": 35, "gender": "male", "smoker": False, "sum_assured": 500000, "term_years": "20"}),
        ("Age 41, female, non-smoker, $1,500,000 over 20 years", {"age": 41, "gender": "female", "smoker": False, "sum_assured": 1500000, "term_years": "20"}),
        ("Age 50, male, smoker, $250,000 over 10 years", {"age": 50, "gender": "male", "smoker": True, "sum_assured": 250000, "term_years": "10"}),
    ],
    "whole_life": [
        ("Age 40, female, non-smoker, $500,000, lifetime pay", {"age": 40, "gender": "female", "smoker": False, "sum_assured": 500000, "premium_schedule": "lifetime"}),
        ("Same, paid up after 20 years", {"age": 40, "gender": "female", "smoker": False, "sum_assured": 500000, "premium_schedule": "20_pay"}),
    ],
    "universal_life": [
        ("Age 43, male, non-smoker, $2,000,000, level death benefit, target premium", {"age": 43, "gender": "male", "smoker": False, "sum_assured": 2000000, "death_benefit_option": "option_a"}),
        ("Same, minimum premium", {"age": 43, "gender": "male", "smoker": False, "sum_assured": 2000000, "death_benefit_option": "option_a", "premium_level": "minimum"}),
    ],
    "variable_life": [
        ("Age 41, female, non-smoker, $1,500,000, level face amount, target premium", {"age": 41, "gender": "female", "smoker": False, "sum_assured": 1500000, "death_benefit_option": "option_a"}),
        ("Same, overfunded", {"age": 41, "gender": "female", "smoker": False, "sum_assured": 1500000, "death_benefit_option": "option_a", "premium_level": "overfund"}),
    ],
    "critical_illness": [
        ("Age 33, male, non-smoker, $100,000, standard tier", {"age": 33, "gender": "male", "smoker": False, "lump_sum": "100000", "illness_tier": "standard"}),
        ("Age 41, male, non-smoker, $250,000, comprehensive tier", {"age": 41, "gender": "male", "smoker": False, "lump_sum": "250000", "illness_tier": "comprehensive"}),
        ("Age 41, male, smoker, $250,000, comprehensive tier, return of premium", {"age": 41, "gender": "male", "smoker": True, "lump_sum": "250000", "illness_tier": "comprehensive", "return_of_premium": True}),
        ("Age 41, female, non-smoker, $250,000, comprehensive tier", {"age": 41, "gender": "female", "smoker": False, "lump_sum": "250000", "illness_tier": "comprehensive"}),
    ],
    "health": [
        ("Age 30, male, non-smoker, standard tier, default choices", {"age": 30, "gender": "male", "smoker": False, "tier": "standard"}),
        ("Age 45, male, non-smoker, essential tier, $25,000 limit, $1,000 deductible", {"age": 45, "gender": "male", "smoker": False, "tier": "essential", "annual_limit": "25000", "deductible": "1000"}),
        ("Age 36, male, non-smoker, premium tier, maternity add-on", {"age": 36, "gender": "male", "smoker": False, "tier": "premium", "maternity": True}),
        ("Age 30, female, non-smoker, standard tier, default choices", {"age": 30, "gender": "female", "smoker": False, "tier": "standard"}),
    ],
    "disability": [
        ("Age 35, male, non-smoker, $5,000 a month, defaults", {"age": 35, "gender": "male", "smoker": False, "monthly_benefit": 5000}),
        ("Age 45, male, non-smoker, $8,000 a month, 180-day wait", {"age": 45, "gender": "male", "smoker": False, "monthly_benefit": 8000, "waiting_period": "180"}),
        ("Age 45, female, non-smoker, $8,000 a month, 180-day wait", {"age": 45, "gender": "female", "smoker": False, "monthly_benefit": 8000, "waiting_period": "180"}),
    ],
    "home": [
        ("$750,000 single-family home, default choices", {"dwelling_value": 750000, "property_type": "single_family"}),
        ("$400,000 condominium with flood protection", {"dwelling_value": 400000, "property_type": "condominium", "flood": True}),
    ],
    "auto": [
        ("Age 41, comprehensive, $25,000 vehicle, default choices", {"age": 41, "cover_type": "comprehensive", "vehicle_value": 25000}),
        ("Age 24, third party only, $8,000 vehicle", {"age": 24, "cover_type": "third_party", "vehicle_value": 8000}),
    ],
    "travel": [
        ("One 10-day worldwide trip, age 35", {"age": 35, "duration": "single_trip", "geographic": "worldwide", "trip_days": 10}),
        ("Annual multi-trip, worldwide, age 45, trips up to 45 days", {"age": 45, "duration": "annual_multi_trip", "geographic": "worldwide", "max_trip_days": "45"}),
    ],
}

_rule = json.loads((OUT / "affordability.json").read_text())
for _name, _examples in EXAMPLES.items():
    _path = rate_file(_name)
    _data = json.loads(_path.read_text())
    _data["examples"] = []
    for _label, _options in _examples:
        _status, _body = _pricing.estimate({"product_type": _name, "options": _options}, None, [], _data, _rule)
        assert _body["status"] == "ok", (_name, _label, _body)
        _data["examples"].append({"label": _label, "options": _options, "annual_premium": _body["annual_premium"]})
    _path.write_text(json.dumps(_data, indent=2, ensure_ascii=False) + "\n")
    print("examples", _name, [e["annual_premium"] for e in _data["examples"]])
