import { useEffect, useState } from "react";

import { ApiError, apiFetch } from "../api/client";

export interface IdentityProfile {
  legal_name: string;
  cui: string;
  county: string | null;
  locality: string | null;
  iban: string | null;
  registered_address: string | null;
  fiscal_domicile: string | null;
  phone: string | null;
  //: when the activity started; drives whether the initial registration (D070) is due
  started_on: string | null;
}

/**
 * Identification data of the PFA (name, CUI, address, IBAN).
 *
 * These live on the versioned fiscal profile, which is created on first write.  When
 * it does not exist yet the workspace's own name and CUI are the effective values,
 * so the fields are never blank for a workspace that was created with a CUI.
 */
export function FiscalIdentityForm({
  workspaceId,
  defaultName,
  defaultCui,
  profile,
  onSaved,
  onError,
}: {
  workspaceId: string;
  defaultName: string;
  defaultCui: string;
  profile: IdentityProfile | null;
  onSaved: (message: string) => void;
  onError: (error: unknown) => void;
}) {
  const [legalName, setLegalName] = useState(defaultName);
  const [cui, setCui] = useState(defaultCui);
  const [county, setCounty] = useState("");
  const [locality, setLocality] = useState("");
  const [iban, setIban] = useState("");
  const [registeredAddress, setRegisteredAddress] = useState("");
  const [fiscalDomicile, setFiscalDomicile] = useState("");
  const [phone, setPhone] = useState("");
  const [startedOn, setStartedOn] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setLegalName(profile?.legal_name || defaultName);
    setCui(profile?.cui || defaultCui);
    setCounty(profile?.county ?? "");
    setLocality(profile?.locality ?? "");
    setIban(profile?.iban ?? "");
    setRegisteredAddress(profile?.registered_address ?? "");
    setFiscalDomicile(profile?.fiscal_domicile ?? "");
    setPhone(profile?.phone ?? "");
    setStartedOn(profile?.started_on ?? "");
  }, [profile, defaultName, defaultCui]);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await apiFetch(`/api/v1/workspaces/${workspaceId}/fiscal-profile`, {
        method: "PATCH",
        body: {
          legal_name: legalName.trim() || defaultName,
          cui: cui.trim(),
          county: county.trim(),
          locality: locality.trim(),
          iban: iban.trim(),
          registered_address: registeredAddress.trim(),
          fiscal_domicile: fiscalDomicile.trim(),
          phone: phone.trim(),
          started_on: startedOn || null,
        },
      });
      onSaved("Datele de identificare au fost salvate.");
    } catch (caught) {
      onError(caught instanceof ApiError ? caught : caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save}>
      <h3>Date de identificare</h3>
      <p className="muted">
        Denumirea și CUI-ul apar pe documente și pe declarații. Sunt preluate din spațiul de lucru
        la creare și pot fi corectate aici.
      </p>

      <div className="field">
        <label htmlFor="profile-legal-name">Denumire PFA</label>
        <input
          id="profile-legal-name"
          value={legalName}
          required
          onChange={(event) => setLegalName(event.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="profile-cui">CUI / CIF</label>
        <input
          id="profile-cui"
          value={cui}
          inputMode="numeric"
          onChange={(event) => setCui(event.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="profile-county">Județ</label>
        <input
          id="profile-county"
          value={county}
          onChange={(event) => setCounty(event.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="profile-locality">Localitate</label>
        <input
          id="profile-locality"
          value={locality}
          onChange={(event) => setLocality(event.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="profile-iban">IBAN</label>
        <input
          id="profile-iban"
          value={iban}
          onChange={(event) => setIban(event.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="profile-address">Adresă sediu social</label>
        <input
          id="profile-address"
          value={registeredAddress}
          onChange={(event) => setRegisteredAddress(event.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="profile-domicile">Domiciliu fiscal</label>
        <input
          id="profile-domicile"
          value={fiscalDomicile}
          onChange={(event) => setFiscalDomicile(event.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="profile-phone">Telefon</label>
        <input
          id="profile-phone"
          value={phone}
          onChange={(event) => setPhone(event.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="profile-started-on">Data începerii activității</label>
        <input
          id="profile-started-on"
          type="date"
          value={startedOn}
          onChange={(event) => setStartedOn(event.target.value)}
        />
        <small className="muted">
          Determinați anul în care se datorează înregistrarea fiscală inițială (D070). Lăsat
          gol, nu se presupune niciun an de început.
        </small>
      </div>

      <button type="submit" className="primary" disabled={busy}>
        Salvează datele de identificare
      </button>
    </form>
  );
}
