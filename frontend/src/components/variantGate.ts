/**
 * The production gate of a generation profile, as the server states it.
 *
 * The server decides whether a profile may be selected for production; the
 * browser only shows the verdict and the reason.  A candidate variant (the
 * official W6A8 low-end candidate, for example) is selectable only when this
 * machine can really execute it - same project, same registered profile, same
 * verified bytes, same validated graph.  When it is not selectable the entry
 * is disabled and points back to the setup page; it is never quietly
 * replaced by another profile.
 */

import { api } from '../api';

export type GateBlocker = { code: string; message: string };

export type ProfileGate = {
  profile_id: string | null;
  profile_name: string | null;
  variant_id: string | null;
  acceptance_status: string | null;
  candidate: boolean;
  selectable: boolean;
  /** Whether the server's verdict could be read at all. */
  readable: boolean;
  blockers: GateBlocker[];
  reference_slots: string[];
  size_mapped: boolean;
};

export const EMPTY_GATE: ProfileGate = {
  profile_id: null,
  profile_name: null,
  variant_id: null,
  acceptance_status: null,
  candidate: false,
  selectable: true,
  readable: false,
  blockers: [],
  reference_slots: [],
  size_mapped: false,
};

/** The gate of every profile of this project, keyed by profile id. */
export async function fetchProfileGates(
  projectId: string,
  profiles: Array<{ id: string }>,
): Promise<Record<string, ProfileGate>> {
  const rows = await Promise.all(profiles.map(async (profile) => {
    try {
      const gate = await api<ProfileGate>(
        `/setup/production-gate?project_id=${encodeURIComponent(projectId)}&profile_id=${encodeURIComponent(profile.id)}`,
      );
      // A verdict that is not a verdict is not a refusal either: the server
      // stays the single gate, and it refuses with its own reason.
      if (!gate || typeof gate.selectable !== 'boolean') {
        return [profile.id, { ...EMPTY_GATE, profile_id: profile.id }] as const;
      }
      return [profile.id, { ...EMPTY_GATE, ...gate, readable: true }] as const;
    } catch {
      return [profile.id, { ...EMPTY_GATE, profile_id: profile.id }] as const;
    }
  }));
  return Object.fromEntries(rows);
}

/** The first reason a profile may not be used, in the words of the server. */
export function gateReason(gate: ProfileGate | undefined): string {
  if (!gate) return '';
  if (gate.selectable) return '';
  return gate.blockers[0]?.message ?? '这台电脑还不能用这个方案。';
}

/** A hint for a profile whose verdict could not be read: the server decides. */
export function gateUnreadable(gate: ProfileGate | undefined): boolean {
  return !!gate && !gate.readable;
}

/** Is this profile one of the candidate stacks (not the audited baseline)? */
export function isCandidateProfile(profile: { model_identity?: Record<string, unknown> } | undefined): boolean {
  const identity = profile?.model_identity ?? {};
  const acceptance = String(identity.variant_acceptance_status ?? '');
  if (!acceptance) return false;
  return acceptance !== 'SUPPORTED' && acceptance !== 'EXPERIMENTAL';
}

/** The variant a profile belongs to, when it declares one. */
export function profileVariantId(profile: { model_identity?: Record<string, unknown> } | undefined): string | null {
  const identity = profile?.model_identity ?? {};
  const variantId = identity.variant_id;
  return typeof variantId === 'string' && variantId ? variantId : null;
}