# Fixtures TTS — phrases de validation réelles (audio versionné)

Ce dossier contient les **entrées audio exactes** des deux validateurs réels
(`scripts/validate_real_gemini.py`, `scripts/validate_reconnect_v174_real.py`).
Ce sont des enregistrements WAV figés : à l'exécution, **aucune requête TTS
n'est émise** (zéro quota Gemini, zéro dépendance réseau) et les entrées sont
identiques d'une exécution à l'autre (rejouabilité R5).

| Fichier | Phrase exacte | Utilisé par |
|---|---|---|
| `bonjour_jarvis_tu_m_entends_bien.wav` | « Bonjour Jarvis, tu m'entends bien ? » | real_gemini S1 |
| `mon_prenom_est_simon.wav` | « Mon prénom est Simon. » | real_gemini S2, v174 |
| `quel_est_mon_prenom.wav` | « Quel est mon prénom ? » | real_gemini S2, v174 |
| `j_habite_a_tours.wav` | « J'habite à Tours. » | real_gemini S3 |
| `ou_est_ce_que_j_habite_deja.wav` | « Où est-ce que j'habite déjà ? » | real_gemini S3 |
| `utilise_l_outil_validation_ping.wav` | « Utilise l'outil validation_ping. » | real_gemini S4 |
| `quel_etait_le_code_retourne.wav` | « Quel était le code retourné par l'outil, déjà ? » | real_gemini S4 |
| `bonjour_jarvis_quelle_heure.wav` | « Bonjour Jarvis, quelle heure est-il ? » | v174 |
| `et_demain_quel_temps.wav` | « Et demain, quel temps fera-t-il ? » | v174 |
| `peux_tu_me_le_confirmer.wav` | « Peux-tu me le confirmer ? » | v174 |

Format : **PCM 16 kHz, mono, 16 bits signés little-endian** — le format
d'entrée du protocole Live du projet (entrée micro). Durée 1,4–6,8 s ;
les validateurs ajoutent ~0,4 s de silence final pour la VAD serveur.

## Garanties

- `manifest.json` est la **source de vérité** : texte exact prononcé, sha256
  du fichier, format, durée. Le loader `scripts/validation/tts_fixtures.py`
  revérifie tout (existence, sha256, format, durées plausibles) AVANT
  l'ouverture de toute session Gemini — une fixture douteuse provoque un
  problème affiché et un repli explicite, jamais un envoi silencieux (R2, R3).
- Les prompts sont appariés **par texte exact** : une phrase du validateur
  qui n'a pas de fixture correspondante retombe sur les backends de secours
  (pyttsx3 local pour v174, TTS Gemini en dernier recours) et le mode
  `--mode fixtures` de `validate_real_gemini.py` échoue explicitement.

## Origine des voix

Voix masculine française « voice-00 » (studio vocal Arena), enregistrée une
fois, convertie 24→16 kHz mono s16le (rééchantillonnage linéaire numpy).
Aucun service de synthèse n'est requis pour rejouer les validations.

## Régénérer / vérifier

```bash
# Revérifier l'ensemble (sha256 + format + durées) :
python scripts/validation/make_tts_fixtures.py check

# Ajouter / remplacer une fixture depuis un WAV source quelconque :
python scripts/validation/make_tts_fixtures.py add \
    --in brut.wav --texte "Phrase exacte prononcée." --out nom_de_fixture
```

**Étape humaine obligatoire** : après toute (re)génération, ÉCOUTER chaque
fichier. La première validation réelle confirme en plus la transcription :
chaque tour affiche « [entendu] <transcription> » — l'écart entre la phrase
demandée et celle reconnue par Gemini signale une fixture corrompue ou
inintelligible (contrôle anti-audio-faux R2).
