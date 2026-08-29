# TRENCHES XDROPS FARM

Bot de volume pour le spot OKX. Il pose un ordre d'achat et un ordre de vente
collés à 1 tick d'écart au sommet du carnet, laisse les autres traders le
remplir, puis revend pour revenir à plat. Il génère du volume de trading —
utile quand une campagne (XDROPS, rebate…) paie ce volume plus cher que ce
qu'il coûte.

**Ce bot PAIE des frais pour créer du volume.** À 8 bp de frais maker, comptez
environ **8 à 10 $ pour 10 000 $ de volume**. Il n'est rentable QUE si une
récompense vous rapporte plus que ça. Pas de campagne = perte garantie.

---

## Étape 1 — Installer (une seule fois)

- **Windows** : double-cliquez sur **`INSTALL_WINDOWS.bat`**. Il vérifie
  Python et l'installe tout seul s'il manque. Suivez ce qu'il affiche.
- **Mac** : ouvrez le Terminal dans le dossier et tapez `bash install_mac.sh`.

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

Ouvrez `bot.py` avec le Bloc-notes (Windows) ou TextEdit (Mac), descendez
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
| `SYMBOL` | la paire spot à farmer, ex. `RE-USDC` |
| `TARGET_VOLUME_USD` | le bot s'arrête après ce volume (achats + ventes) |

**Zone 2 — réglages avancés** (les valeurs par défaut sont saines) :

| Variable | Rôle |
|---|---|
| `HOST` | `eea.okx.com` (Europe) ou `www.okx.com` (global) |
| `CLIP_USD` | taille d'un ordre quand le marché est actif |
| `CLIP_USD_QUIET` | taille quand le marché est calme (le seuil se mesure tout seul) |
| `INVENTORY_BAND_USD` | plafond dur de coins détenus ; l'achat ralentit puis s'arrête en approchant |
| `LOSS_CAP_MULT` | ligne d'arrêt : stoppe à `frais taker × ceci` par 10 000 $ ; mettez-la juste SOUS ce que votre récompense paie par 10 000 $ |

Financez le compte avec au moins **2× `CLIP_USD` en devise de cotation**
(ex. USDC) plus la marge de `INVENTORY_BAND_USD`.

## Étape 5 — Lancer

- **Windows** : double-cliquez sur **`START_WINDOWS.bat`**.
- **Mac** : dans le Terminal, tapez `python3 bot.py`.

Le bot affiche la paire, le volume visé, les frais estimés et la ligne d'arrêt,
puis demande de taper `YES`. Tapez `YES` et Entrée. Ensuite un panneau se met à
jour en continu :

```
volume    ce que vous avez tradé, et le rythme par minute
cost      ce que ça vous a coûté, en $ et par 10 000 $ — avec la ligne d'arrêt
          fees = les frais (fixes) | drift = le marché qui bouge contre vous
inv       coins détenus vs le plafond
book      le carnet, et vos ordres dedans
```

## Étape 6 — Arrêter

- **Ctrl+C** dans la fenêtre : arrêt propre — le bot annule ses ordres et
  revend son inventaire (jusqu'à 2 minutes), puis affiche un bilan `FINAL`.
- **Fermer la fenêtre d'un coup : à éviter** — des ordres peuvent rester posés.
  (Le prochain lancement les annule, mais seulement si vous relancez.)

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
   frais taker (bp) × `LOSS_CAP_MULT`. Exemple : récompense de 14 $/10k et
   frais taker 10 bp → mettez 1,39 pour stopper à 13,90 $/10k.
5. Conçu pour VIP0/VIP1 (8 bp maker / 10 bp taker). Le bot vérifie vos vrais
   frais au démarrage et refuse de tourner s'ils sont pires.

## Dépannage

- `fill in API_KEY / API_SECRET / API_PASSPHRASE` : les clés sont vides —
  retournez à l'Étape 3.
- `HTTP 401` : clé/secret/passphrase faux, ou `HOST` ne correspond pas à votre
  type de compte (Europe vs global).
- `account fees are worse...` : votre palier de frais dépasse 8/10 bp — le bot
  refuse car le modèle de coût ne tient plus.
- Panneau figé / caractères bizarres : utilisez Windows Terminal ou le Terminal
  Mac ; le bot bascule tout seul en affichage simple si la console ne suit pas.
