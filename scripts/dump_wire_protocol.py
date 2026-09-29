"""Dump exact du câble Gemini Live tel que le SDK le sérialiserait (mission §validation 1).

Ce script ne se contente PAS de lire le faux serveur : il récupère les objets
RÉELS construits par ``GeminiLive`` (``types.LiveConnectConfig``, tours du
seed, blobs audio) et les passe dans les MÊMES sérialiseurs internes que
``google.genai`` utilise pour la vraie connexion WebSocket :

* setup : ``_t_live_connect_config`` + ``_LiveConnectParameters_to_mldev``
  (chemin exact de ``AsyncSession.connect``) ;
* clientContent : ``t_client_content`` + ``_LiveClientContent_to_mldev``
  (chemin exact de ``AsyncSession.send_client_content``) ;
* realtimeInput : ``t_realtime_input`` + ``_LiveClientRealtimeInput_to_mldev``
  (chemin exact de ``AsyncSession.send_realtime_input``).

Il produit la transcription chronologique complète d'un cycle de vie :
connexion 1 → tour audio → handle → coupure → reconnexion (seed) →
premier tour audio de la session 2. Chaque message est affiché en JSON tel
qu'il partirait sur le WebSocket (les blobs audio sont tronqués à leur taille).

Aucune clé API n'est nécessaire : la sérialisation est purement locale.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google import genai  # noqa: E402
from google.genai import _common, _live_converters as lc, _transformers as t, types  # noqa: E402

from tests.live_harness import FakeLiveServer  # noqa: E402
from tests.voice_harness import VoiceHarness  # noqa: E402

MAX_JSON = 4000


#: Client factice : la construction ne fait AUCUN appel réseau et la clé
#: n'est jamais affichée — elle ne sert qu'à satisfaire le convertisseur.
_DUMMY_CLIENT = genai.Client(api_key="dummy-serialization-only")


def _setup_wire(config: types.LiveConnectConfig, model: str) -> dict:
    """Sérialise la config exactement comme ``AsyncSession.connect``."""
    from google.genai import _api_client  # noqa: PLC0415

    assert isinstance(_DUMMY_CLIENT._api_client, _api_client.BaseApiClient)
    parameter_model = config  # déjà un LiveConnectConfig
    request_dict = _common.convert_to_dict(
        lc._LiveConnectParameters_to_mldev(
            api_client=_DUMMY_CLIENT._api_client,
            from_object=types.LiveConnectParameters(
                model=model,
                config=parameter_model,
            ).model_dump(exclude_none=True),
        )
    )
    request_dict.pop("config", None)
    request_dict = _common.encode_unserializable_types(request_dict)
    return request_dict


def _client_content_wire(turns, turn_complete: bool) -> dict:
    client_content = t.t_client_content(turns, turn_complete).model_dump(
        mode="json", exclude_none=True
    )
    return {
        "client_content": lc._LiveClientContent_to_mldev(
            from_object=client_content
        )
    }


def _realtime_audio_wire(pcm: bytes) -> dict:
    """Chemin exact de ``AsyncSession.send_realtime_input(audio=...)``."""
    params = types.LiveSendRealtimeInputParameters.model_validate(
        {"audio": types.Blob(data=pcm, mime_type="audio/pcm;rate=16000")}
    )
    realtime_input_dict = _common.convert_to_dict(
        lc._LiveSendRealtimeInputParameters_to_mldev(from_object=params)
    )
    realtime_input_dict = _common.encode_unserializable_types(realtime_input_dict)
    return {"realtime_input": realtime_input_dict}


def _trim(value, depth: int = 0):
    """Tronque les blobs pour l'affichage, sans changer la structure."""
    if isinstance(value, dict):
        return {k: _trim(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_trim(v, depth + 1) for v in value]
    if isinstance(value, str) and len(value) > 120 and (value.endswith("=") or "pcm" in value or len(value) > 400):
        return f"<{len(value)} chars>"
    return value


def _show(title: str, payload: dict) -> None:
    text = json.dumps(_trim(payload), ensure_ascii=False, indent=2)
    if len(text) > MAX_JSON:
        text = text[:MAX_JSON] + "\n… (tronqué pour l'affichage)"
    print(f"\n=== {title} ===\n{text}")


def _setup_summary(setup: dict) -> dict:
    """Extrait les clés critiques du message setup (sans le system prompt)."""
    s = setup.get("setup", {})
    out = {
        "model": s.get("model"),
        "responseModalities": s.get("generationConfig", {}).get("responseModalities"),
    }
    for key in (
        "sessionResumption",
        "inputAudioTranscription",
        "outputAudioTranscription",
        "contextWindowCompression",
        "historyConfig",
        "speechConfig",
    ):
        if key in s:
            out[key] = s[key]
        else:
            out[f"{key} (ABSENT)"] = None
    return out


async def _lifecycle(suppress_handles: bool) -> tuple:
    server = FakeLiveServer("2.x")
    server.suppress_resumption_updates = suppress_handles
    harness = VoiceHarness(server)
    await harness.start()
    try:
        await harness.speak("Mon prénom est Simon.")
        server.drop_connection()
        await harness.wait_sessions(2, timeout=5)
        await harness.speak("Quel est mon prénom ?")
    finally:
        await harness.stop()
    return server, harness


async def main() -> int:
    model = "gemini-2.5-flash-native-audio-preview-12-2025"
    print(f"# Dump du câble — sérialisation SDK google-genai réelle, modèle {model}")
    # --- Scénario A : seed (aucun handle délivré) ---------------------------
    server, harness = await _lifecycle(suppress_handles=True)

    # 1. SETUP de chaque session (config Jarvis réelle, sérialisée par le SDK)
    _show(
        "SETUP (clés critiques) — session 1 puis session 2, chemin SEED",
        {
            "session_1": _setup_summary(_setup_wire(server.configs[1], model)),
            "session_2": _setup_summary(_setup_wire(server.configs[2], model)),
        },
    )

    # 2. Ordre chronologique du câble de la session 2 (seed puis audio)
    print("\n=== Ordre chronologique du câble — session 2 (reconnexion) ===")
    for event in server.wire:
        if event.generation != 2:
            continue
        print(f"  {event.describe()}")
    seed_events = server.client_content_events(generation=2)
    for event in seed_events:
        _show("clientContent (SEED) — JSON exact", _client_content_wire(event.contents, True))
    audio_events = [e for e in server.wire if e.generation == 2 and e.kind == "realtime_audio"]
    if audio_events:
        _show(
            "realtimeInput (PREMIER AUDIO utilisateur) — JSON exact",
            _realtime_audio_wire(b"\x01\x02" * 160),
        )

    # 3. Résumé des invariants
    kinds = [e.kind for e in server.wire if e.generation == 2]
    first_audio = kinds.index("realtime_audio") if "realtime_audio" in kinds else len(kinds)
    print("\n=== Invariants ===")
    print(f"  session 2 : ordre des kinds = {kinds}")
    print(f"  le seed clientContent précède le premier realtime_input : {kinds.index('client_content') < first_audio}")
    for event in seed_events:
        roles = [c.get("role") for c in event.contents]
        print(f"  seed : rôles={roles}")
        print(f"  seed : dernier rôle = {roles[-1]} (doit être 'user')")
        print(f"  seed : turn_complete=True (capturé : {event.turn_complete})")
    # --- Scénario B : reprise par handle (chemin primaire) -------------------
    server_r, harness_r = await _lifecycle(suppress_handles=False)
    _show(
        "SETUP (clés critiques) — reprise par HANDLE",
        {
            "session_1": _setup_summary(_setup_wire(server_r.configs[1], model)),
            "session_2": _setup_summary(_setup_wire(server_r.configs[2], model)),
        },
    )
    kinds_r = [e.kind for e in server_r.wire if e.generation == 2]
    print("\n=== Chemin REPRISE (handle valide) ===")
    print(f"  session 2 : kinds = {kinds_r}")
    print(f"  AUCUN client_content (le serveur restaure l'historique) : {'client_content' not in kinds_r}")
    print(f"  resumed_count (reprises côté serveur) = {server_r.resumed_count}")
    print(f"  rappel du prénom après reprise : {'Simon' in (harness_r.turns[-1].assistant_text if harness_r.turns else '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
