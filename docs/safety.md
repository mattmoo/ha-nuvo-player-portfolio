# Safety rules

Enforced in code by `custom_components/nuvo_player/aionuvo/safety.py` (`check_action`). Every SOAP call in
`aionuvo`, the shim and `tools/` goes through it.

## Denylist (never call)

| Action | Why |
|---|---|
| `RestoreFactoryDefaults` | Wipes zone/system configuration. |
| `SystemCreate` | Rebuilds the Nuvo system topology. |
| `SystemJoin` | Moves a zone between systems. |
| `SystemConfigureGWs` | Reconfigures gateways. |

Any HTTP POST to `/firmware.fcgi` or `/firmwareupdate.fcgi` (advertised as the
zone's `UpdateURL`) is also forbidden.

The guard raises `DeniedActionError` for these unless the environment has
`NUVO_I_KNOW=1`. Do not set that variable in normal use.

## Web UI JSON API

The web UI JSON API writes through plain GET requests (`/api/setData`), so every
write there is denied. A narrow tone-control exception (bass, treble, balance)
existed from 2026-09-28 until it was **withdrawn the same day**: the writes were
stored but never applied to the sound (PLAN.md, "Tone and loudness"). Any new
exception needs a user decision recorded here first.

## Write actions (need user approval during recon)

Read-only calls (`Get*`, description/SCPD GETs, SUBSCRIBE) are always fine.
These change device state, so during Phase 1 each one needs explicit user
approval, is run one at a time, and has its before-state recorded first:

`SetVolume` (start low), `SetMute`, `X_NUVO_AdjustVolume`, `GroupCreate`,
`GroupDisband`, `GroupMemberSetGroup`, `UserTap`, `X_NUVO_PlayContainerURI`,
`X_NUVO_PlayURI`, `SetAVTransportURI`, `Play`, `Pause`, `Stop`, `Next`,
`Previous`, `Seek`, `SetPlayMode`, `SetLoudness`, `SetVolumeDB`,
`SelectPreset` (its only allowed value is `FactoryDefaults`, which resets
the zone's rendering settings), `UserDoubleTap`, `X_NUVO_Like`,
`X_NUVO_Dislike`, `X_NUVO_TogglePause`, `X_NUVO_PauseOrStop`,
`X_NUVO_BeginPrevious`, and the ContentDirectory editors `UpdateObject`,
`X_NUVO_AddChild`, `X_NUVO_MoveChild`, `X_NUVO_RemoveChild`,
`X_NUVO_ClearChildren` (these edit favourites and queues). `X_NUVO_Action`
and `X_NUVO_Action2` are opaque, so treat them as writes too.

`tools/` scripts refuse any action outside the read-only set unless run with
`--allow-write`.

## Before write testing

Record the current app configuration (zone names, groups, favourites) in
`docs/protocol.md`.
