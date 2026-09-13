/**
 * ISO data mirrored from the server's pycountry validators
 * (`src/huntloop/criteria/schema.py`). The client validates first so the user
 * gets an inline error before the request; the server remains the authority.
 * Keep these lists in parity with pycountry — they are generated data, not
 * hand-curated guesses.
 */

export const SENIORITY_LADDER = [
  "intern",
  "junior",
  "mid",
  "senior",
  "staff",
  "principal",
  "director",
  "vp",
  "c_level",
] as const

export type Seniority = (typeof SENIORITY_LADDER)[number]

export const SENIORITY_LABELS: Record<Seniority, string> = {
  intern: "Intern",
  junior: "Junior",
  mid: "Mid-level",
  senior: "Senior",
  staff: "Staff",
  principal: "Principal",
  director: "Director",
  vp: "VP",
  c_level: "C-level",
}

export const VALID_REGIONS = ["EMEA", "APAC", "LATAM", "NA", "EU"] as const

export const COMP_PERIODS = ["annual", "monthly", "hourly"] as const
export type CompensationPeriod = (typeof COMP_PERIODS)[number]

// pycountry.countries alpha_2, sorted.
const ALPHA2_COUNTRY_CODES = (
  "AD,AE,AF,AG,AI,AL,AM,AO,AQ,AR,AS,AT,AU,AW,AX,AZ,BA,BB,BD,BE,BF,BG,BH,BI,BJ," +
  "BL,BM,BN,BO,BQ,BR,BS,BT,BV,BW,BY,BZ,CA,CC,CD,CF,CG,CH,CI,CK,CL,CM,CN,CO,CR," +
  "CU,CV,CW,CX,CY,CZ,DE,DJ,DK,DM,DO,DZ,EC,EE,EG,EH,ER,ES,ET,FI,FJ,FK,FM,FO,FR," +
  "GA,GB,GD,GE,GF,GG,GH,GI,GL,GM,GN,GP,GQ,GR,GS,GT,GU,GW,GY,HK,HM,HN,HR,HT,HU," +
  "ID,IE,IL,IM,IN,IO,IQ,IR,IS,IT,JE,JM,JO,JP,KE,KG,KH,KI,KM,KN,KP,KR,KW,KY,KZ," +
  "LA,LB,LC,LI,LK,LR,LS,LT,LU,LV,LY,MA,MC,MD,ME,MF,MG,MH,MK,ML,MM,MN,MO,MP,MQ," +
  "MR,MS,MT,MU,MV,MW,MX,MY,MZ,NA,NC,NE,NF,NG,NI,NL,NO,NP,NR,NU,NZ,OM,PA,PE,PF," +
  "PG,PH,PK,PL,PM,PN,PR,PS,PT,PW,PY,QA,RE,RO,RS,RU,RW,SA,SB,SC,SD,SE,SG,SH,SI," +
  "SJ,SK,SL,SM,SN,SO,SR,SS,ST,SV,SX,SY,SZ,TC,TD,TF,TG,TH,TJ,TK,TL,TM,TN,TO,TR," +
  "TT,TV,TW,TZ,UA,UG,UM,US,UY,UZ,VA,VC,VE,VG,VI,VN,VU,WF,WS,YE,YT,ZA,ZM,ZW"
).split(",")

// pycountry.currencies alpha_3, sorted.
const ALPHA3_CURRENCY_CODES = (
  "AED,AFN,ALL,AMD,AOA,ARS,AUD,AWG,AZN,BAM,BBD,BDT,BHD,BIF,BMD,BND,BOB,BOV," +
  "BRL,BSD,BTN,BWP,BYN,BZD,CAD,CDF,CHE,CHF,CHW,CLF,CLP,CNY,COP,COU,CRC,CUP," +
  "CVE,CZK,DJF,DKK,DOP,DZD,EGP,ERN,ETB,EUR,FJD,FKP,GBP,GEL,GHS,GIP,GMD,GNF," +
  "GTQ,GYD,HKD,HNL,HTG,HUF,IDR,ILS,INR,IQD,IRR,ISK,JMD,JOD,JPY,KES,KGS,KHR," +
  "KMF,KPW,KRW,KWD,KYD,KZT,LAK,LBP,LKR,LRD,LSL,LYD,MAD,MDL,MGA,MKD,MMK,MNT," +
  "MOP,MRU,MUR,MVR,MWK,MXN,MXV,MYR,MZN,NAD,NGN,NIO,NOK,NPR,NZD,OMR,PAB,PEN," +
  "PGK,PHP,PKR,PLN,PYG,QAR,RON,RSD,RUB,RWF,SAR,SBD,SCR,SDG,SEK,SGD,SHP,SLE," +
  "SOS,SRD,SSP,STN,SVC,SYP,SZL,THB,TJS,TMT,TND,TOP,TRY,TTD,TWD,TZS,UAH,UGX," +
  "USD,USN,UYI,UYU,UYW,UZS,VED,VES,VND,VUV,WST,XAD,XAF,XAG,XAU,XBA,XBB,XBC," +
  "XBD,XCD,XCG,XDR,XOF,XPD,XPF,XPT,XSU,XTS,XUA,XXX,YER,ZAR,ZMW,ZWG"
).split(",")

/**
 * The bounded value lists the criteria form's dropdowns render. Exported as
 * the single source of truth shared with the validators below — a control can
 * only ever offer a value `isAlpha2Country` / `isAlpha3Currency` accepts, so
 * the form cannot present a value the server's Pydantic validators would 422.
 */
export const COUNTRY_CODES = ALPHA2_COUNTRY_CODES as readonly string[]
export const CURRENCY_CODES = ALPHA3_CURRENCY_CODES as readonly string[]

const COUNTRY_SET = new Set(ALPHA2_COUNTRY_CODES)
const CURRENCY_SET = new Set(ALPHA3_CURRENCY_CODES)
const REGION_SET: ReadonlySet<string> = new Set(VALID_REGIONS)

export function isAlpha2Country(value: string): boolean {
  return COUNTRY_SET.has(value.toUpperCase())
}

export function isAlpha3Currency(value: string): boolean {
  return CURRENCY_SET.has(value.toUpperCase())
}

export function isRegion(value: string): boolean {
  return REGION_SET.has(value.toUpperCase())
}
