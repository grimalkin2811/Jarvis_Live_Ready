# Procédure de validation réelle v1.8 (Windows)

Cette procédure complète les tests automatisés. Un test au faux serveur, une
réponse injectée ou un test unitaire ne doit jamais être reporté comme une
validation réelle.

## Sécurité

Le script `scripts/validate_v180_real.py` ne possède aucun argument de clé. Il
lit `GEMINI_API_KEY`, `GOOGLE_API_KEY` ou la configuration Jarvis existante,
garde la valeur en mémoire et masque les motifs de clés dans toute sortie et
dans le rapport JSON. Ne collez jamais la clé dans une ligne de commande,
un fichier de rapport ou une capture d’écran.

## 1. Préflight sans appel API

Dans PowerShell, depuis la racine du dépôt et avec l’environnement Python de
Jarvis :

```powershell
py -3 scripts\validate_v180_real.py --preflight
```

Le contrôle couvre Windows, configuration, SDK Gemini, NumPy, sounddevice et
PortAudio, PySide6, ONNX Runtime, openWakeWord, pyttsx3, périphériques audio,
modèles wake-word et création d’un widget Qt avec traitement d’événements.
`NON_TESTABLE` n’est jamais un succès.

## 2. API et tâches réelles

```powershell
py -3 scripts\validate_v180_real.py --api
```

Cette commande effectue de vraies connexions/requêtes :

- ouverture Gemini 2.5 Flash Native Audio ;
- découverte du modèle Gemini 3 Live généraliste et validation par échange réel
  AUDIO → `output_transcription` non vide → signal terminal officiel
  (`generation_complete`, `turn_complete` ou `interaction_status=IDLE`), puis décisions
  Router A/B/C ; les variantes transcription/traduction/thinking sont filtrées
  et chaque candidat refusé conserve sa catégorie et son diagnostic ;
- Google Search grounding sur le modèle Live résolu ;
- cycle simple, résultat, non-vu puis vu ;
- tâche complexe et document avec Gemini 3.8 Flash ;
- deux tâches simultanées avec chronologies indépendantes et état de quota
  local isolé ; si 3.8 est déjà refusé par Google, deux tâches Live sont testées
  réellement et la partie Live+Flash reste `NON_TESTABLE` ;
- connexion Gemini 2.5 pendant que les deux tâches sont actives ;
- compteurs 3.8 et relecture persistante ;
- refus local à la limite sans consommer d’appel supplémentaire ;
- erreur API réelle contrôlée vers un modèle volontairement inexistant, puis
  nouvelle connexion du modèle principal.

Sans retry, le run consomme normalement **au plus deux appels Gemini 3.8**.
Chaque tentative 429/503 réellement envoyée est toutefois comptée et peut
augmenter ce total dans les limites configurées. Il ne tente jamais d’épuiser
volontairement RPM/RPD/TPM. Les données du test sont placées dans un dossier
temporaire afin de ne pas polluer l’historique utilisateur.

Le rapport sépare `EXTERNAL_QUOTA`, `EXTERNAL_SERVICE`,
`LIVE_RESOURCE_EXHAUSTED`, `LOCAL_QUOTA_GUARD`, `TIMEOUT`, `LIVE_PROTOCOL` et
`CODE_OR_PROTOCOL`. Une 1011 avec `You exceeded your current quota` est
`EXTERNAL_QUOTA`; une 1011 avec le seul motif `Resource has been exhausted` est
`LIVE_RESOURCE_EXHAUSTED`. Aucune limite RPM/TPM/RPD précise n'est inventée. Chaque tâche simultanée inclut sa chronologie.
Un document ne passe que si le résultat est non vide et si tous les fichiers
référencés existent et sont non vides. Chaque tâche rapporte également le
transport réellement exécuté (`Live/BidiGenerateContent` ou `GenerateContent`),
son signal de fin Live et les URI de grounding disponibles. Vérifier dans le
rapport qu'une décision `complex` annonce bien `transport=GenerateContent`.

Le rapport JSON est écrit dans le dossier de logs utilisateur. Un autre chemin
peut être donné avec `--report`, jamais avec une clé.

## 3. S1 → S6 et reconnexion réels

```powershell
py -3 scripts\validate_v180_real.py --s1-s6
```

Cette commande délègue au harnais réel existant
`scripts/validate_reconnect_v174_real.py`. Ses verdicts détaillés S1+S4, S2,
S3, S5 et S6 restent l’autorité. Le wrapper ne transforme pas une absence de
clé ou un environnement non-Windows en succès.

## 4. Non-blocage, audio, UI et notification — observation humaine obligatoire

Afficher le protocole :

```powershell
py -3 scripts\validate_v180_real.py --manual-protocol
```

Puis lancer Jarvis normalement :

```powershell
py -3 run_jarvis.py --ui
```

L’opérateur doit observer réellement : confirmation immédiate, tâche complexe
active pendant au moins trois échanges Native Audio, micro/transcription/TTS
fonctionnels, deux tâches indépendantes, compteurs Qt, progression, détail,
fichier, annulation, non-lu→vu et notification de fin silencieuse pendant une
réponse. Une connexion API automatisée ne remplace pas cette observation audio.

## Verdict

`READY_FOR_RELEASE` n’est autorisé que si :

1. le préflight Windows ne contient aucun échec critique ;
2. `--api` passe avec les identifiants réellement autorisés au compte ;
3. S1→S6 passe précisément ;
4. le protocole manuel audio/UI passe intégralement ;
5. la suite automatisée complète passe dans l’environnement Windows.

Aucune de ces commandes ne crée de tag ou de release.
