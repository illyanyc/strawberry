import {
  type PublicKeyCredentialCreationOptionsJSON,
  type PublicKeyCredentialRequestOptionsJSON,
  startAuthentication,
  startRegistration,
} from "@simplewebauthn/browser";

import { api } from "./api";

interface CeremonyBegin<T> {
  ceremony_id: string;
  options: T;
}

/** Run a registration ceremony: begin → browser passkey prompt → finish. */
export async function registerPasskey(
  beginPath: string,
  finishPath: string,
  beginBody: Record<string, unknown> | undefined,
  finishExtra: Record<string, unknown>,
): Promise<unknown> {
  const begin = await api<CeremonyBegin<PublicKeyCredentialCreationOptionsJSON>>(beginPath, {
    method: "POST",
    body: beginBody ?? {},
    allow401: true,
  });
  const credential = await startRegistration({ optionsJSON: begin.options });
  return api(finishPath, { method: "POST", body: { ...finishExtra, ceremony_id: begin.ceremony_id, credential }, allow401: true });
}

/** Run an assertion ceremony (login or step-up). */
export async function assertPasskey(beginPath: string, finishPath: string): Promise<unknown> {
  const begin = await api<CeremonyBegin<PublicKeyCredentialRequestOptionsJSON>>(beginPath, {
    method: "POST",
    body: {},
    noStepUp: true,
    allow401: true,
  });
  const credential = await startAuthentication({ optionsJSON: begin.options });
  return api(finishPath, { method: "POST", body: { ceremony_id: begin.ceremony_id, credential }, noStepUp: true, allow401: true });
}
