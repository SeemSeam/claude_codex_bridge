<div align="center">

# CCB - L'application mobile est arrivée !

**Un TUI multi-agent léger avec une couche de collaboration stable entre fournisseurs**<br>
**Coordonne Codex, Claude, Gemini et d'autres agents CLI dans des workflows visibles, contrôlables et reprenables directement**

<p>
  <img src="https://img.shields.io/badge/version-8.6.6-orange.svg" alt="version">
  <img src="https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20WSL-lightgrey.svg" alt="platform">
  <img src="https://img.shields.io/badge/providers-17%20CLI%20families-0B7285.svg" alt="providers">
</p>

<p>
  <img src="https://img.shields.io/badge/Codex-111111?style=flat-square&logo=openai&logoColor=white" alt="Codex">
  <img src="https://img.shields.io/badge/Claude-D97757?style=flat-square&logo=anthropic&logoColor=white" alt="Claude">
  <img src="https://img.shields.io/badge/Gemini-4285F4?style=flat-square&logo=googlegemini&logoColor=white" alt="Gemini">
  <img src="https://img.shields.io/badge/Grok-000000?style=flat-square&logo=x&logoColor=white" alt="Grok CLI">
  <img src="https://img.shields.io/badge/Kimi-111111?style=flat-square&logo=moonshotai&logoColor=white" alt="Kimi">
  <img src="https://img.shields.io/badge/MiMo-FF6900?style=flat-square&logo=xiaomi&logoColor=white" alt="MiMo">
  <img src="https://img.shields.io/badge/Qwen-6A5CFF?style=flat-square" alt="Qwen">
  <img src="https://img.shields.io/badge/Cursor-111111?style=flat-square" alt="Cursor">
  <img src="https://img.shields.io/badge/Copilot-111111?style=flat-square&logo=githubcopilot&logoColor=white" alt="GitHub Copilot">
  <img src="https://img.shields.io/badge/Crush-FF5A5F?style=flat-square" alt="Crush">
  <img src="https://img.shields.io/badge/Kiro-6D5EF6?style=flat-square" alt="Kiro">
  <img src="https://img.shields.io/badge/Pi-111111?style=flat-square" alt="Pi">
  <img src="https://img.shields.io/badge/Z.ai-111111?style=flat-square" alt="Z.ai">
  <img src="https://img.shields.io/badge/OpenCode-111111?style=flat-square" alt="OpenCode">
  <img src="https://img.shields.io/badge/Antigravity-6D5EF6?style=flat-square&logo=google&logoColor=white" alt="Antigravity">
  <img src="https://img.shields.io/badge/Droid-3DDC84?style=flat-square&logo=android&logoColor=white" alt="Droid">
</p>

[中文](zh.md) | [English](../README.md) | [日本語](ja.md) | **Français** | [Deutsch](de.md) | [العربية](ar.md) | [Español](es.md) | [Português](pt.md) | [한국어](ko.md) | [Русский](ru.md)

[Démarrage rapide](#quick-start) · [Mobile App](#mobile-app) · [Mode Rich](#rich-mode) · [Configurer les agents](#configure-agents) · [Guide utilisateur](../docs/manuals/user-guide/) · [Guide développeur](../docs/manuals/developer-guide/)

<p align="center">
  <img src="../assets/readme_v7/ccb-hero-en-light.png" alt="Espace de travail CLI multi-agent visible de CCB" width="960">
</p>

</div>

<a id="why-ccb"></a>

## Pourquoi CCB ?

- Une communication inter-agent stable pour des graphes de collaboration complexes comme `A -> B -> C`, `A,B -> C` et `A -> B,C`.
- Chaque agent est un vrai terminal natif, avec une disposition visible et une prise de contrôle directe.
- Le daemon d'arrière-plan conserve l'état du projet même lorsque l'interface au premier plan est fermée.
- Capacité Hub : exécuter plusieurs CLI providers en parallèle depuis une seule commande.
- Contrôleur mobile distant : contrôle vocal multi-provider, transfert de fichiers et accès terminal distant.

<a id="how-to-install"></a>

## Comment installer

Installez ou mettez à jour une installation gérée par npm avec npm :

```bash
npm install -g @seemseam/ccb@latest
```

Pour une installation GitHub release ou source, utilisez l'updater transactionnel de CCB :

```bash
ccb update
```

Dans une installation gérée par npm, `ccb update` affiche la commande npm équivalente sans modifier le payload embarqué.

<details>
<summary><b>Paquets GitHub release et installation source en secours</b></summary>

Si npm n'est pas pratique dans votre environnement, téléchargez le paquet adapté depuis [Releases](https://github.com/SeemSeam/claude_codex_bridge/releases), décompressez-le puis installez-le :

```bash
tar -xzf ccb-*.tar.gz
cd ccb-*
./install.sh install
```

L'installation depuis les sources est réservée au développement ou à un contournement temporaire :

```bash
git clone https://github.com/SeemSeam/claude_codex_bridge.git
cd claude_codex_bridge
./install.sh install
```

Une installation source relie les commandes globales `ccb` / `ask` au checkout courant. Les utilisateurs ordinaires devraient préférer le paquet npm.

</details>

<a id="quick-start"></a>

## Démarrage rapide

### 1. Lancer

Exécutez depuis votre répertoire de travail :

```bash
ccb
```

Si le démarrage indique que `.ccb` ne peut pas être créé automatiquement ou que l'ancre du projet est absente, créez `.ccb` manuellement :

```bash
mkdir -p .ccb
```

<a id="configure-agents"></a>

### 2. Créer la configuration du projet

Un projet vierge démarre de façon légère : CCB ouvre une seule window `main` avec un agent nommé `demo` et sélectionne le premier CLI pris en charge disponible sur la machine. Aucune équipe multi-agent n'est montée par défaut.

Cliquez sur **⚙ Paramètres** en haut à gauche de la sidebar CCB pour ouvrir le panneau de configuration local. Vous pouvez aussi exécuter `ccb config ui`.

<p align="center">
  <img src="../assets/readme_v7/config-control-panel.png" alt="Panneau de configuration CCB pour l'agent demo par défaut" width="960">
</p>

Le panneau configure les windows, divisions de panes, providers, modèles, niveaux de thinking, API overrides, workspaces, mode Rich et sidebar. Il valide avant l'enregistrement et prend en charge le reload dry-run et le hot reload protégé.

Pour une topologie multi-agent avancée, ajoutez des agents visuellement ou créez `.ccb/ccb.config` manuellement. `,` et `;` contrôlent les empilements verticaux et divisions horizontales ; `A,B;C,D` correspond environ à quatre panes.

```toml
version = 2

[windows]
main = "main:codex"
work = "worker1:codex(worktree), worker2:claude(worktree)"
review = "reviewer:claude, qa:gemini"

[ui.sidebar]
mode = "every_window"
width = "15%"
bottom_height = 20
agents_height = "50%"
comms_height = "15%"
tips_height = "35%"
comms_limit = 3
```

Validez la configuration puis lancez l'espace de travail :

```bash
ccb config validate
ccb
```

### 3. Collaborer

Vous pouvez saisir directement dans n'importe quel agent pane, ou faire collaborer les agents :

```text
/ask reviewer review the latest parser changes and list blocking issues.
```

Les agents peuvent aussi appeler `/ask` pendant l'orchestration d'un workflow pour déléguer et transmettre le travail. Utilisez la mémoire d'agent ou le fichier de mémoire partagée du projet `.ccb/ccb_memory.md` pour une coordination durable.

<a id="mobile-app"></a>

## Contrôle mobile distant (Android)

La méthode recommandée pour contrôler CCB depuis un téléphone peut se connecter à tous les projets CCB, piloter chaque agent, accepter la saisie vocale et transférer des fichiers.

```bash
ccb update mobile
```

Cette commande guide l'installation et la configuration.

<p align="center">
  <img src="../assets/readme_v7/mobile-control-chat.jpg" alt="Conversation agent CCB Mobile" width="180">
  <img src="../assets/readme_v7/mobile-control-terminal.jpg" alt="Contrôle terminal CCB Mobile" width="180">
  <img src="../assets/readme_v7/mobile-control-files.jpg" alt="Transfert de fichiers CCB Mobile" width="180">
  <img src="../assets/readme_v7/mobile-control-pairing.jpg" alt="Appairage et connexion CCB Mobile" width="180">
</p>

<details>
<summary><b>Détails de l'app mobile, frontière de sécurité et source</b></summary>

CCB 8.6.6 inclut le code source Flutter de CCB Mobile dans [`mobile/`](../mobile/) et publie l'APK Android via GitHub Releases :

- [Télécharger l'APK CCB Mobile v8.6.6](https://github.com/SeemSeam/claude_codex_bridge/releases/download/v8.6.6/ccb-mobile-v8.6.6.apk)
- Source de l'app : [`mobile/app`](../mobile/app)
- Source du gateway serveur : [`lib/mobile_gateway`](../lib/mobile_gateway)

L'application mobile est un contrôleur distant pour de vrais projets CCB exécutés sur un serveur. Elle peut découvrir les projets montés depuis le mobile gateway server-wide, changer de window/agent, afficher le contexte de conversation des agents, envoyer du texte via l'entrée pane-native, ouvrir une vue terminal, et charger/télécharger images et documents via le gateway authentifié.

Frontière de sécurité :

- Le gateway CCB se lie uniquement au loopback, par exemple `127.0.0.1:8787`.
- L'accès distant utilise Tailscale Serve, pas Tailscale Funnel.
- CCB ne stocke pas les mots de passe Tailscale, tokens OAuth, tokens admin API, et ne modifie pas automatiquement les ACL/grants du tailnet.
- Le téléphone reçoit uniquement les scopes autorisés par le pairing profile, comme view, content, terminal, file upload et file download.

</details>

<a id="rich-mode"></a>

## Terminal média Rich

Parcourez l'arborescence, ouvrez des fichiers, modifiez des documents et prévisualisez des médias dans le terminal.

<p align="center">
  <img src="../assets/readme_v7/rich-workbench.png" alt="Workbench média Rich de CCB avec aperçu Yazi dans WezTerm" width="860">
</p>

```bash
ccb update rich
```

Une fois le mode rich activé, `ccb` ouvre automatiquement le rich WezTerm launcher sauf s'il s'exécute déjà dans une session rich WezTerm gérée par CCB. Exécutez `ccb uninstall rich` pour revenir au démarrage terminal normal.

<a id="agent-roles"></a>

## Agent Roles Spec et catalogue de rôles

CCB prend en charge [Agent Roles Spec](https://github.com/SeemSeam/agent-roles-spec), une spécification host-neutral pour empaqueter des agents spécialisés. Elle peut regrouper skills, mémoire et dépendances d'outils dans des Role Packs installables, montables et supprimables. Ce dépôt sert aussi de catalogue public de rôles.

| Role | Objectif |
| :--- | :--- |
| `agentroles.ccb_self` | Auto-maintenance CCB, aide à la configuration, diagnostic runtime, récupération protégée et orchestration de workflow. |
| `agentroles.archi` | Revue d'architecture, vérification des frontières, analyse du couplage, risques de maintenabilité et conseils de gate. |
| `agentroles.frontend_engineer` | Design et implémentation frontend, design systems, accessibilité, QA navigateur et délégation AGY revue. |
| `agentroles.mobile_app_engineer` | Design et implémentation mobile pour iOS, Android, React Native, Expo, Flutter, SwiftUI, Jetpack Compose, etc. |
| `agentroles.mother` | Création de rôles, audit de source de rôle, recherche de rôles, conception de blueprint et contrôles de conformité Agent Roles. |
| `agentroles.su_ccb` | Opérations workflow SU-CCB pour analyse des besoins, planification, dispatch, review gates, archivage et récupération. |

<a id="config-memory"></a>

## Configuration et mémoire partagée

Pour la configuration courante du projet, utilisez le panneau **⚙ Paramètres**. Pour une configuration assistée par agent et le diagnostic runtime, `ccb_self` reste disponible comme Role Pack optionnel et peut être ajouté avec `ccb roles add agentroles.ccb_self:codex`.

`.ccb/ccb_memory.md` est le document de mémoire partagée du projet. Utilisez-le pour les règles de collaboration d'équipe, les contraintes de projet, le contexte durable et les conventions de passation entre agents. Les informations stables entre agents doivent y vivre plutôt que d'être copiées dans plusieurs fichiers de mémoire privés des providers.

<a id="contact"></a>

## Contact

- Email: `bfly123@126.com`
- [Telegram group & contact / TG 群与联系](https://t.me/+BKn03v8I_ehmYzRk)
- WeChat: `seemseam-com`

<p align="center">
  <img src="../assets/weixin.png?v=7335e843" alt="Groupe WeChat" width="240">
</p>

<a id="community"></a>

## Communauté et crédits

Merci à la [communauté Linux.do](https://linux.do) pour les tests, retours et discussions.

Merci à [tmux-agent-sidebar](https://github.com/hiroppy/tmux-agent-sidebar) pour les idées et l'inspiration autour de la sidebar.

<a id="release-notes"></a>

## Notes de version

<details open>
<summary><b>v8.4.0</b> - Relay mobile chiffré, appairage simple, identité stable et reconnexion Codex</summary>

- Ajoute un Relay chiffré de bout en bout, des invitations à usage unique, des flux multiplexés et des modes officiel ou auto-hébergé.
- Déplace le choix Tailscale, LAN privé ou Relay dans `ccb update mobile` ; le téléphone ne fait que scanner un QR ou saisir un code.
- Vérifie les métadonnées GitHub officielles, la taille et le SHA-256 avant de confier l'APK signé à Android.
- Préserve l'identité après déplacement du projet, suit le thème système et intègre une reconnexion Codex optionnelle et bornée.

</details>

<details>
<summary><b>v8.3.1</b> - Mises à jour provider unifiées, retrait sûr des caches et accès Config UI persistant</summary>

- Centralise les mises à niveau provider prises en charge dans `ccb update`, avec vérification exacte, refus et saut limité à une version, sans redémarrer les panes actifs.
- Retire les caches logiciels Claude/Gemini par projet et ne nettoie que les données historiques dont CCB peut prouver la propriété ; projets actifs, sessions et authentification sont préservés.
- Autorise un port loopback stable et une source de token protégée pour Config UI sans afficher la valeur du token.
- Préserve les finalizers de shutdown pendant l'arrêt du serveur et adopte une vue Yazi compacte à deux colonnes en Rich mode.
- Synchronise CLI, npm, Linux, macOS, Android et tous les artefacts de release avec 8.3.1.

</details>

<details>
<summary><b>v8.3.0</b> - Tours provider exacts, intégrité des jobs et terminal Mobile lié au projet</summary>

- Lie Kimi, Claude et Qoder à leurs contrats natifs de tour, d'activation, de session et d'achèvement.
- Ajoute les follow-ups du job actif exact, les phases d'exécution corrélées, le diagnostic des inbounds orphelins et l'annulation terminale.
- Hérite des extensions provider et des plugins Copilot avec une protection explicite de l'ownership des assets projetés.
- Délègue les mises à niveau gérées par npm à npm et retire prudemment les worktrees ne contenant que des marqueurs.
- Maintient le chat et le terminal Mobile dans le workspace du projet sélectionné et synchronise toutes les surfaces de release avec 8.3.0.

</details>

<details>
<summary><b>v8.2.1</b> - Démarrage déterministe, récupération d'authentification explicite et connexion Android en arrière-plan</summary>

- Ajoute des barrières de génération de démarrage, une preuve de disponibilité bornée et des diagnostics chronologiques.
- Arrête les boucles de redémarrage sans issue liées à l'authentification provider et affiche l'action de connexion requise.
- Ajoute une connexion Android en arrière-plan sur activation et un seul état de réponse active par Agent.
- Synchronise les artefacts Linux, macOS, npm et Android signé avec la version 8.2.1.

</details>

<details>
<summary><b>v8.2.0</b> - Démarrage accéléré, correctifs provider et fiabilité Mobile</summary>

- Réduit le travail répété au démarrage de ccbd sans affaiblir les contrôles de lifecycle et d'ownership.
- Corrige le démarrage fullscreen de Grok, préserve le type d'identifiant Claude, stabilise les choix model/thinking et renforce la livraison ask/reply Codex.
- Améliore la reprise, le chat, le terminal, les pièces jointes, les téléchargements et FCM dans Mobile ; les artifacts Linux, macOS, npm et Android signé ciblent 8.2.0.

</details>

<details open>
<summary><b>v8.0.14</b> - Rangement du dossier README et surface mobile</summary>

- Le `README.md` racine redevient la page GitHub en anglais.
- Les README localisés vivent maintenant dans [`README/`](./), avec le chinois dans [`zh.md`](zh.md).
- Les liens Mobile App, les métadonnées package et les notes de version pointent vers l'APK 8.0.14.

</details>

<details>
<summary><b>v8.0.12</b> - Portabilité CI de release et localisation du README</summary>

- Les tests mobile host registry placent maintenant leurs sockets Unix temporaires sous un chemin court `/tmp/ccb-sock-*`, ce qui évite les échecs `AF_UNIX path too long` sur la CI macOS.
- `ccb update mobile`, les liens README, les métadonnées package et le mobile release manifest pointent maintenant vers l'APK 8.0.12.
- v8.0.12 a introduit les README multilingues avec une structure commune ; les fichiers localisés actuels vivent dans le dossier `README/`.

</details>

<details>
<summary><b>v8.0.0</b> - Publication du monorepo CCB Mobile</summary>

- Le source Flutter de CCB Mobile a officiellement rejoint ce dépôt, avec l'APK Android publié via GitHub Releases.
- Ajout de la découverte server-wide des projets mobiles, appairage, routes gateway authentifiées, saisie pane-native, rendu du contexte de conversation, accès terminal, et chargement/téléchargement d'images et documents.
- `ccb update mobile` devient le point d'entrée unifié d'onboarding Tailscale Tailnet, tout en gardant le gateway en loopback-only, sans Funnel, sans stockage de tokens et sans modification automatique des ACL/grants.

</details>

<details>
<summary><b>v7.7.0</b> - Durcissement de la publication Runtime Accelerator</summary>

- Les artifacts de release incluent maintenant le `ccb-runtime-accelerator` Rust optionnel ; les agents Codex installés ne retombent plus silencieusement sur le chemin Python lorsque le sidecar est attendu.
- Quand le chemin du projet rend le chemin Unix socket trop long, le socket de l'accelerator bascule automatiquement vers une racine runtime courte par utilisateur.
- Durcissement de callback repair et de l'invalidation du cache de binding Codex, avec preuves de régression, long-idle Codex soak, callback Claude et intégration mixed-provider.

</details>

<details>
<summary><b>v7.6.19</b> - Politique d'attente par défaut pour les ask longs</summary>

- Les `ask` longs continuent désormais d'attendre les vrais résultats provider/completion au lieu de terminer en `incomplete/heartbeat_timeout` uniquement à cause des diagnostics heartbeat.
- Les no-terminal timeouts pane-backed de Codex, Claude et Gemini sont maintenant opt-in explicite par défaut, tout en conservant les politiques de reliability timeout explicites.
- Un smoke source-runtime ask de 32 minutes a confirmé qu'une tâche peut rester running plus de 30 minutes puis se terminer avec `result_message`, sans preuve `heartbeat_timeout` ni `incomplete`.

</details>

Voir l'historique complet dans [CHANGELOG.md](../CHANGELOG.md).
