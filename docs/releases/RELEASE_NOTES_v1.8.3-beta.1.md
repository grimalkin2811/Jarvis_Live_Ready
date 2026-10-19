# Jarvis v1.8.3-beta.1 — bêta du Dashboard

> **Préversion de test. Ne remplacez pas votre installation stable v1.8.2 pour un usage critique.**
>
> Cette candidate provient de la branche d'intégration du Dashboard. Elle n'est pas considérée comme une version stable tant que la validation vocale réelle sur Windows n'a pas été concluante.

## Objectif

Introduire le Dashboard comme troisième interface native PySide6, en complément de l'orbe et du Desktop Mode. Le Dashboard doit partager le même moteur, la même conversation et les mêmes services ; il ne doit pas remplacer l'orbe ni modifier le mode d'interface par simple ouverture.

## Fonctionnalités prévues dans cette bêta

- Six pages intégrées et widgets configurables, avec pages personnalisées, mode édition, OnePage et préréglages.
- Thèmes Jarvis, Edith et Friday.
- Conversation écrite utilisant le contexte du moteur vocal partagé.
- Affichage des tâches, de la mémoire, des notifications, des diagnostics et des réglages.
- Ouverture depuis le menu de notification, le raccourci Ctrl+D de l'orbe, la ligne de commande et les outils vocaux.
- Tests automatisés Windows et vérifications de performance/packaging Qt.

## État de validation connu au 19 octobre 2026

- La suite automatisée et le lint Windows sont passés sur la tête d'intégration examinée.
- Les validations Gemini Live réelles exécutées dans GitHub Actions étaient **non testables** faute de clé API ; elles ne doivent pas être interprétées comme des succès.
- Le protocole réel Windows de l'utilisateur a signalé un échec lors de l'ouverture vocale du Dashboard pendant une conversation active (E02). Le repli déterministe est couvert par des tests unitaires, mais le scénario réel reste à corriger et à revalider.
- Des tests de reconnexion et d'appel d'outils ont également présenté des résultats insuffisants dans le protocole réel. Ne pas affirmer que ces parcours sont fiables avant une nouvelle exécution avec Gemini Live réel.
- Le comportement de l'orbe ne doit pas être considéré comme intégralement validé par les seuls tests unitaires du Dashboard.

## Consignes de bêta-test

1. Conserver une copie de sauvegarde de la configuration et de la mémoire avant le test.
2. Vérifier le démarrage et l'utilisation normale de l'orbe sans ouvrir le Dashboard.
3. Tester séparément l'ouverture/fermeture du Dashboard, les messages écrits, les tours vocaux successifs et l'ouverture pendant une conversation.
4. Tester la reconnexion et les outils vocaux dans une session Gemini Live réelle.
5. Signaler les résultats avec les journaux expurgés de toute clé API, jeton ou donnée personnelle.

## Critère de sortie de bêta

La publication stable ne doit intervenir qu'après une validation Windows réelle concluante de l'orbe, de plusieurs tours vocaux, des outils, de la reconnexion et des scénarios Dashboard E01–E10, puis une reconstruction et une vérification de l'installateur et de l'archive portable.
