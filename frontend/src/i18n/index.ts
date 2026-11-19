/**
 * Romanian-first copy with an English translation.
 *
 * Form codes are always secondary to a readable name: the dictionary stores the
 * readable label and, separately, the code that may accompany it.
 */

export type Language = "ro" | "en";

export interface Dictionary {
  appName: string;
  nav: Record<
    "home" | "income" | "expenses" | "settlements" | "declarations" | "settings",
    string
  >;
  actions: Record<
    | "addDocument"
    | "verify"
    | "save"
    | "login"
    | "logout"
    | "switchWorkspace"
    | "generate"
    | "download"
    | "uploadReceipt"
    | "sendToAnaf"
    | "markRead"
    | "why",
    string
  >;
  labels: Record<
    | "activePfa"
    | "deadline"
    | "paymentDeadline"
    | "period"
    | "issuedRevenue"
    | "collectedRevenue"
    | "estimatedTaxes"
    | "needsAttention"
    | "nextDeadlines"
    | "incompleteRecords"
    | "missingInformation"
    | "received"
    | "paid"
    | "amount"
    | "date"
    | "source"
    | "partialPayment"
    | "status"
    | "ruleVersion"
    | "noItems"
    | "loading"
    | "uploadInstructions",
    string
  >;
  status: Record<string, string>;
  disclaimer: Record<"estimate" | "unknownDeadline", string>;
}

const ro: Dictionary = {
  appName: "Contabilitate PFA",
  nav: {
    home: "Acasă",
    income: "Venituri",
    expenses: "Cheltuieli",
    settlements: "Încasări și plăți",
    declarations: "Declarații",
    settings: "Setări",
  },
  actions: {
    addDocument: "Adaugă document",
    verify: "Verifică datele",
    save: "Salvează",
    login: "Autentificare",
    logout: "Deconectare",
    switchWorkspace: "Schimbă PFA",
    generate: "Generează",
    download: "Descarcă pentru ANAF",
    uploadReceipt: "Încarcă recipisa",
    sendToAnaf: "Trimite la ANAF",
    markRead: "Marchează ca citit",
    why: "Cum s-a calculat?",
  },
  labels: {
    activePfa: "PFA activ",
    deadline: "Termen de depunere",
    paymentDeadline: "Termen de plată",
    period: "Perioada",
    issuedRevenue: "Venituri emise",
    collectedRevenue: "Venituri încasate",
    estimatedTaxes: "Estimare taxe (internă)",
    needsAttention: "Necesită atenție",
    nextDeadlines: "Termene următoare",
    incompleteRecords: "Documente incomplete",
    missingInformation: "Informații lipsă",
    received: "Ai încasat?",
    paid: "Ai plătit?",
    amount: "Sumă",
    date: "Data",
    source: "Sursă bancă/casă",
    partialPayment: "Plată parțială",
    status: "Stare",
    ruleVersion: "Versiune reguli",
    noItems: "Nu există înregistrări încă.",
    loading: "Se încarcă…",
    uploadInstructions: "Instrucțiuni de încărcare",
  },
  status: {
    required: "obligatorie",
    not_required: "nu se aplică",
    needs_information: "necesită informații",
    overdue: "restanță",
    due: "scadent astăzi",
    upcoming: "în termen",
    rejected: "respinsă",
    accepted: "acceptată",
    draft: "ciornă",
    finalized: "finalizată",
    calculated: "calculată",
    validated: "validată",
    approved: "aprobată",
    exported: "generată",
    awaiting_external_filing: "de depus în SPV",
    queued: "în coadă",
    submitted: "transmisă",
    submission_unknown: "stare de transmitere necunoscută",
  },
  disclaimer: {
    estimate: "Valorile estimate sunt calcule interne. Nu sunt solduri confirmate de ANAF.",
    unknownDeadline: "Termen de verificat",
  },
};

const en: Dictionary = {
  appName: "PFA Accounting",
  nav: {
    home: "Home",
    income: "Income",
    expenses: "Expenses",
    settlements: "Receipts and payments",
    declarations: "Declarations",
    settings: "Settings",
  },
  actions: {
    addDocument: "Add document",
    verify: "Check the data",
    save: "Save",
    login: "Sign in",
    logout: "Sign out",
    switchWorkspace: "Switch PFA",
    generate: "Generate",
    download: "Download for ANAF",
    uploadReceipt: "Upload receipt",
    sendToAnaf: "Send to ANAF",
    markRead: "Mark as read",
    why: "How was this calculated?",
  },
  labels: {
    activePfa: "Active PFA",
    deadline: "Filing deadline",
    paymentDeadline: "Payment deadline",
    period: "Period",
    issuedRevenue: "Issued revenue",
    collectedRevenue: "Collected revenue",
    estimatedTaxes: "Internal tax estimate",
    needsAttention: "Needs attention",
    nextDeadlines: "Upcoming deadlines",
    incompleteRecords: "Incomplete records",
    missingInformation: "Missing information",
    received: "Have you been paid?",
    paid: "Have you paid?",
    amount: "Amount",
    date: "Date",
    source: "Bank/cash source",
    partialPayment: "Partial payment",
    status: "Status",
    ruleVersion: "Rule version",
    noItems: "No records yet.",
    loading: "Loading…",
    uploadInstructions: "Upload instructions",
  },
  status: {
    required: "required",
    not_required: "not applicable",
    needs_information: "needs information",
    overdue: "overdue",
    due: "due today",
    upcoming: "on time",
    rejected: "rejected",
    accepted: "accepted",
    draft: "draft",
    finalized: "finalized",
    calculated: "calculated",
    validated: "validated",
    approved: "approved",
    exported: "generated",
    awaiting_external_filing: "to be filed in SPV",
    queued: "queued",
    submitted: "submitted",
    submission_unknown: "unknown submission state",
  },
  disclaimer: {
    estimate: "Estimated values are internal calculations, not ANAF-confirmed balances.",
    unknownDeadline: "Deadline to be verified",
  },
};

const dictionaries: Record<Language, Dictionary> = { ro, en };

export function dictionary(language: Language): Dictionary {
  return dictionaries[language] ?? ro;
}

/** Readable status text; colour is never the only status indicator. */
export function statusLabel(language: Language, status: string): string {
  return dictionary(language).status[status] ?? status;
}

export const obligationFormNames: Record<string, string> = {
  D212: "Declarația unică (D212)",
  D300: "Decontul de TVA (D300)",
  D301: "Decontul special de TVA (D301)",
  D390: "Declarația recapitulativă VIES (D390)",
  D394: "Declarația informativă (D394)",
  D700: "Modificări de înregistrare fiscală (D700)",
  D070: "Înregistrare fiscală (D070)",
  D112: "Declarația de salarizare (D112)",
  D100: "Obligații la buget (D100)",
  D205: "Informativ rețineri (D205)",
  D207: "Informativ venituri independente (D207)",
  D710: "Rectificativă informativă (D710)",
  D307: "Ajustări TVA bunuri de capital (D307)",
  D311: "Ajustări TVA la anulare (D311)",
  D398: "Regim special OSS/IOSS (D398)",
  EFACTURA: "RO e-Factura",
  D406: "SAF-T (D406)",
};

export function formLabel(formCode: string): string {
  return obligationFormNames[formCode] ?? formCode;
}
