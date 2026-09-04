# TRENCHES XDROPS FARM

Bot de volume pour le spot OKX. Il pose un ordre d'achat et un ordre de vente
collés à 1 tick d'écart au sommet du carnet, laisse les autres traders le
remplir, puis revend pour revenir à plat. Il génère du volume de trading —
utile quand une campagne (XDROPS, rebate…) paie ce volume plus cher que ce
qu'il coûte.

**Ce bot PAIE des frais pour créer du volume.** À 8 bp de frais maker, comptez
environ **8 à 10 $ pour 10 000 $ de volume**. Il n'est rentable QUE si une
récompense vous rapporte plus que ça. Pas de campagne = perte garantie.

Les valeurs par défaut sont volontairement petites : **$140 exposés au maximum**
(deux ordres + la bande d'inventaire), cible de $5 000 de volume, arrêt
automatique vers $6 de perte. N'augmentez les tailles qu'après avoir observé
un run complet.

Vérifiez la taille minimale d'ordre de votre paire avant de réduire `CLIP_USD` :
en dessous, le bot ne peut plus coter du tout.

---

## Étape 1 — Installer (une seule fois)

Tout ce qu'il vous faut est dans UN dossier : `windows`, `mac` ou `linux` (bot +
installeur + lanceur).

- **Windows** : ouvrez le dossier **`windows`** et double-cliquez sur
  **`INSTALL.bat`**. Il vérifie Python et l'installe tout seul s'il manque.
- **Mac** : ouvrez le dossier **`mac`** et double-cliquez sur
  **`install.command`** (la première fois : clic droit → Ouvrir, macOS bloque
  le double-clic simple sur un fichier inconnu).
- **Linux** : dans le dossier **`linux`**, lancez `bash install.sh` depuis un
  terminal. Il installe Python via apt / dnf / pacman / zypper / apk selon
  votre distribution.

Rien d'autre à installer — aucune librairie, aucun `pip install`.

## Étape 2 — Créer une clé API OKX

1. Sur OKX : **Profil → API → Créer une clé API**.
2. Permissions : cochez **Lecture** et **Trade**. **NE COCHEZ JAMAIS Retrait.**
3. Mettez une **liste blanche d'IP** si vous connaissez votre IP fixe (recommandé).
4. Notez les trois valeurs : clé, secret, passphrase. Le secret ne sera plus
   jamais affiché.

⚠️ Compte OKX **Europe** (my.okx.com / eea.okx.com) par défaut. Si votre compte
est OKX global (www.okx.com), changez `HOST` dans les réglages (voir Étape 4).

## Étape 3 — Donner les clés au bot

Ouvrez le `bot.py` **de votre dossier** (`windows`, `mac` ou `linux`) avec le
Bloc-notes ou TextEdit, descendez
**tout en bas** jusqu'au bandeau `1. YOUR SETTINGS`, et collez vos trois
valeurs entre les guillemets :

```python
API_KEY = "votre_clé"
API_SECRET = "votre_secret"
API_PASSPHRASE = "votre_passphrase"
```

Enregistrez. C'est tout — vos clés ne quittent jamais ce fichier, qui reste
sur votre machine.

## Étape 4 — Régler le bot

Tout en bas de `bot.py`, deux zones :

**Zone 1 — obligatoire :**

| Variable | Rôle |
|---|---|
| `API_KEY` / `API_SECRET` / `API_PASSPHRASE` | vos clés |
| `SYMBOL` | la paire spot à farmer, ex. `CP-USDC`. Vérifiez quelle paire la campagne compte vraiment — sur RE seule la paire USDC rapportait, le volume USDT ne comptait pas |
| `TARGET_VOLUME_USD` | le bot s'arrête après ce volume (achats + ventes) |

**Zone 2 — réglages avancés** (les valeurs par défaut sont saines) :

| Variable | Rôle |
|---|---|
| `HOST` | `eea.okx.com` (Europe) ou `www.okx.com` (global) |
| `CLIP_USD` | taille d'un ordre quand le marché est actif |
| `CLIP_USD_QUIET` | taille quand le marché est calme (le seuil se mesure tout seul) |
| `INVENTORY_BAND_USD` | plafond dur de coins détenus ; l'achat ralentit puis s'arrête en approchant |
| `BUSY_PAUSE_SECONDS` | pause après avoir posé ou annulé ; l'attente complète ne s'applique qu'au repos |
| `LOSS_CAP_MULT` | ligne d'arrêt en $ par 10 000 $ de volume : `REFUSE_IF_TAKER_BP_OVER × ceci`. À 10,0 × 1,20 le bot s'arrête vers 12 $/10k. Mettez-la juste SOUS ce que votre récompense paie par 10 000 $ |
| `REFUSE_IF_MAKER_BP_OVER` | **ce ne sont pas vos frais.** Le bot lit votre vrai palier chez OKX au démarrage et refuse de tourner s'il est pire que ça |
| `REFUSE_IF_TAKER_BP_OVER` | idem côté taker. Sert aussi de base au calcul de la ligne d'arrêt ci-dessus |

Financez le compte avec au moins **2× `CLIP_USD` en devise de cotation**
(ex. USDC) plus la marge de `INVENTORY_BAND_USD`.

Le bot affiche tout ça en clair avant de demander `YES` : volume visé, taille
des ordres, frais attendus, ligne d'arrêt — chaque chiffre aussi ramené au
**$ par 10 000 $ de volume**, la seule unité comparable à ce que paie une
campagne.

Cinq réglages plus fins (`WARMUP_VOLUME_USD`, `SPEND_FRACTION`,
`MAX_RUNTIME_SECONDS`, `UNWIND_SECONDS`, `QUIET_FLOW_USD_PER_MIN`) gardent
leurs valeurs par défaut dans `_config` : son docstring documente chacun, son
contrat et sa valeur. À noter, celui qui surprend le plus : le loss cap ne
s'arme qu'après **4× `INVENTORY_BAND_USD`** de volume, donc un bot qui perd
dans ses premières minutes ne s'arrêtera pas tout de suite — c'est voulu, un
PnL de départ n'est qu'un fill chanceux ou non.

## Étape 5 — Lancer

- **Windows** : double-cliquez sur **`windows\START.bat`**.
- **Mac** : double-cliquez sur **`mac/start.command`**.
- **Linux** : lancez **`./linux/start.sh`**.

Le bot affiche la paire, le volume visé, les frais estimés et la ligne d'arrêt,
puis demande de taper `YES`. Tapez `YES` et Entrée. Ensuite un panneau se met à
jour en continu :

```
volume    ce que vous avez tradé, et le rythme par minute
cost      ce que ça vous a coûté, en $ et par 10 000 $ — avec la ligne d'arrêt
          fees = les frais (fixes) | drift = le marché qui bouge contre vous
inv       coins détenus vs le plafond
book      le carnet, et vos ordres dedans
last      la dernière action, ou l'erreur complète si elle échoue
```

Le bot n'écrit aucun fichier : tout reste dans ce panneau. Une erreur longue
de l'exchange se replie sur plusieurs lignes au lieu d'être tronquée.

## Étape 6 — Arrêter

- **Ctrl+C** dans la fenêtre : arrêt propre — le bot annule ses ordres et
  revend son inventaire (jusqu'à 2 minutes), puis affiche un bilan `FINAL`.
Les ordres sont aussi annulés sur toute autre sortie que l'interpréteur
contrôle encore : une erreur non gérée, un `exit` normal, ou un SIGTERM sur Mac
et Linux.

- **Le seul cas non couvrable : un kill brutal** (`taskkill /F`, Fin de tâche,
  coupure de courant). Le processus meurt sans exécuter la moindre ligne et les
  ordres restent posés — jusqu'au prochain démarrage du bot, qui les annule.

## Quand il s'arrête tout seul

- **Objectif atteint** : `TARGET_VOLUME_USD` fait.
- **Ligne d'arrêt (loss cap)** : la perte a dépassé la limite — presque
  toujours un marché en tendance qui déprécie l'inventaire, pas un bug. Le bot
  explique quoi faire. Relancer remet le compteur de perte à zéro : ne relancez
  pas en boucle dans un marché qui vous arrête sans arrêt.
- Frais du compte pires que prévu, panne d'API, ou carnet vide 120 s.

## À savoir absolument (état partagé)

1. **Le bot ne trade que ce qu'il achète lui-même.** Vos coins existants sont
   photographiés au démarrage et jamais vendus.
2. **Un seul bot par compte et par paire.** Le démarrage annule TOUS les ordres
   classiques ouverts sur la paire (les grids / TP-SL ne sont pas touchés).
   Ne pointez jamais ce bot sur une paire qu'un autre bot (grid, DCA) ou vous-
   même tradez aussi.
3. **Relancer remet la limite de perte à zéro** (voir ci-dessus).
4. **`LOSS_CAP_MULT` encode VOTRE campagne.** Formule : arrêt en $/10k =
   `REFUSE_IF_TAKER_BP_OVER` × `LOSS_CAP_MULT`. Exemple : récompense de 14 $/10k et
   frais taker 10 bp → mettez 1,39 pour stopper à 13,90 $/10k.
5. Conçu pour VIP0/VIP1 (8 bp maker / 10 bp taker). Le bot vérifie vos vrais
   frais au démarrage et refuse de tourner s'ils sont pires.

## Dépannage

- `fill in API_KEY / API_SECRET / API_PASSPHRASE` : les clés sont vides —
  retournez à l'Étape 3.
- `HTTP 401` **au moment de poser un ordre, alors que le panneau s'affiche
  déjà** : la clé est valide mais en lecture seule. Le démarrage lit vos frais
  et votre solde avec la même signature — s'il est allé jusque-là, il ne manque
  que la permission **Trade** (OKX → Profil → API → votre clé). Le bot s'arrête
  désormais avec ce message au lieu de réessayer indéfiniment.
- `HTTP 401` **dès le démarrage** : clé/secret/passphrase faux, `HOST` qui ne
  correspond pas à votre type de compte (Europe vs global), ou une liste
  blanche d'IP qui ne vous inclut pas. Le bot se connecte en **IPv4** :
  comparez la liste blanche à <https://api.ipify.org>, pas à une page qui
  répond en IPv6 — cette adresse-là ne pourra jamais correspondre.
- `your real OKX fees are...` : votre palier dépasse 8/10 bp — le bot
  refuse car le modèle de coût ne tient plus.
- Panneau figé / caractères bizarres : utilisez Windows Terminal ou le Terminal
  Mac ; le bot bascule tout seul en affichage simple si la console ne suit pas.
