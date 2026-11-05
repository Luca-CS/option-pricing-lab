# Handoff : les politiques de pricing d'optlab (5 novembre 2026)

Objectif de Luca : passer d'un prix « un modèle par produit » à un prix de desk. Un **prix de base en local vol**, auquel s'ajoutent des provisions explicites :
bumps de marché choisis par le trader, lissage des digitales, provisions de modèle. Le tout avec des défauts par groupe de sous-jacents et par taille, modifiables
dans le Pricer, et un récapitulatif des coûts de couverture à la fin.

Ce document est le point d'entrée de la session. Partir de la branche `data` (commit `ec3e96d` ou suivant), non mergée. Contexte : `CLAUDE.md`, `optlab/app/DESIGN.md`,
`reports/showcase.md` pour voir le desk tourner. Luca préfère des constructeurs pilotés par schéma et masques, et des itérations « noyau d'abord, puis fonctionnalités ».

## 1. Décisions déjà prises par Luca

| Sujet | Décision |
|---|---|
| Sens des bumps | **Signé par le trader.** Chaque défaut porte son signe et s'applique tel quel |
| Worst-of sur plusieurs groupes | **Le plus prudent**, paramètre par paramètre, entre les groupes de ses noms |
| Au-dessus du dernier palier de taille | Le Pricer alerte et demande une saisie manuelle |
| Provisions après booking | **Figées à l'émission et amorties** sur la vie du produit, libérées au rappel |
| Prix de base | Local vol. Puis, selon la famille, les modèles qui capturent les risques propres à cette famille. L'écart est gardé comme provision fixe, **seulement s'il est positif** (exemple de Luca : autocall = LV + provision de vol stochastique si positive) |
| Worst-ofs | **Étendre SLV aux paniers, et implémenter un modèle de skew de corrélation**, avec chacun sa provision |
| Digitales | Lissage par call spreads, avec `delta max` et `min spread` définis par groupe, modifiables dans le Pricer |

Les paramètres à bumper : vol (décalage parallèle), corrélation, dividendes, repo, skew de vol, skew de corrélation, « etc. » (§4).

## 2. Ce qui existe, à lire avant de coder

| Sujet | État | Où |
|---|---|---|
| Choix du modèle | Une règle par classe : european → réplication, path_dependent et callable → **SLV**, basket → LV. Le contrôle est seulement affiché | `structured/policy.py` |
| Devis | Solde le coupon de façon affine sur nombres aléatoires communs, `control`, `mix_band` | `structured/quote.py`, `solve.py` |
| Bumps | Spot par `spot_factors`, vol parallèle par `ShiftedSurface` et `BasketContext.bumped`. Rien sur le skew, les dividendes, le repo, la corrélation | `structured/greeks.py`, `multiasset.py` |
| **Lissage des digitales** | **Pas ce que Luca demande.** Seule une rampe symétrique (`Evaluator.eps`, 0.5 %) sert aux Greeks ; son biais est mesuré. Aucun call spread dans le prix | `script/evaluator.py:_compare` |
| Marge et émetteur | `wrapper.margin`, `issuer_spread`, `funding_spread`, banque fictive | `structured/schema.py`, `marketdata/derive/bank.py` |
| Univers | Registre déclaratif, sans groupes | `marketdata/universe.py` |
| Forward | Un seul flux de dividendes, repo et dividende non séparés. `equity_forward` sait traiter un repo mais n'est pas branché | `marketdata/derive/carry.py`, `pit.py` |
| Corrélation | Matrice réalisée avec prime COR, `skewed_matrix`, `nearest_correlation`, `CorrelationSkew` (une hypothèse), pseudo CvC/PvP `basket_spreads` | `marketdata/derive/correl.py` |
| SLV | `calibrate_leverage` par nom (particules). `simulate_basket` **sait déjà** faire avancer une variance par nom, mais `BasketContext.model` et `decide` refusent `slv` | `slv.py`, `multiasset.py:60-95`, `policy.py:85` |
| Book | Positions sur une simulation par groupe, `Trade` versionné en SQLite, aucune notion de réserve | `structured/portfolio.py`, `trade.py`, `app/repository.py` |

**Prérequis bloquant.** Dans le Pricer et le Book, seul le nom du marché actif a une surface : les autres noms d'un worst-of sont en Black-Scholes plat
(`Desk._basket_ctx`), et une fiche sur un autre nom est refusée. Seul le replay utilise `pit.basket_at`. Un SLV panier, un skew de corrélation ou des défauts par groupe n'ont pas de sens là-dessus.

## 3. Le prix cible

```
Base                local vol (réplication pour une européenne : même loi, sans bruit)
+ Provisions modèle  max(0, écart défavorable au desk) par modèle de la famille, figées
+ Bumps marché       un coût par paramètre, signés par le trader
+ Digitales          coût du lissage en call spreads
= Prix client, puis le coupon est soldé sur ce prix et la marge
```

- **Récapitulatif « Hedging cost »** dans le Pricer : ligne, défaut du groupe, valeur utilisée, surchargée ou non, coût en % du nominal avec SE, effet sur le coupon en bp. Une ligne d'interaction
  (tous les bumps ensemble moins la somme des bumps seuls) ; le prix final utilise tous les bumps ensemble. C'est mon choix par défaut, à confirmer.
- **Nombres aléatoires communs partout,** sinon les petites provisions se perdent dans le bruit. Afficher une SE sur chaque différence.
- **Le solde du coupon.** Chaque prix est affine en coupon sur des trajectoires communes, mais `max(0, ·)` ne l'est pas : itérer le solde (deux passes suffisent sans doute, à vérifier).
- **Tout est stocké** dans le devis et le trade, et apparaît dans le rapport HTML.

## 4. Les bumps

| Paramètre | Effet proposé | Remarque |
|---|---|---|
| Vol parallèle | `ShiftedSurface`, en points | Existe. En LV, pas de recalibration de levier, donc bon marché |
| Skew de vol | Rotation autour de l'ATM, définie en points d'écart 90-110 % à 1 an, avec une échelle en maturité à préciser | À écrire avec ses dérivées (Dupire). Contrôler l'arbitrage statique (`svi_arbitrage_report`) et alerter |
| Corrélation | Décalage des paires, matrice réparée par `nearest_correlation` | Worst-ofs seulement |
| Skew de corrélation | Pente du modèle du §7 | Dépend de la phase P7 |
| Dividendes | En % du dividende ou en points de rendement | Via le flux, ou `exp(-dq T)` pour une calibration fichier |
| Repo | En bp | Dans ce modèle, repo et dividende bougent le même forward : séparer les lignes pour le coût, pas pour la dynamique |
| Spot | En bp, pour le passage de bid-ask du delta | Luca a parlé de bid-ask : à confirmer |
| Barrière quotidienne | Décalage de barrière | Une barrière continue ne se lisse pas par un call spread |

Taux et spread émetteur existent déjà dans le wrapper. Pas de FX : aucune source n'est chargée, et le quanto n'existe pas (hors périmètre, à traiter à part).

## 5. Groupes et tailles

- `Instrument` gagne un `group` (ex. indice US, indice EU, action US grande capitalisation : liste à valider).
- Une table de défauts par groupe et par palier de taille maximum : bumps, `delta max`, `min spread`. Rien en dur : éditable depuis un écran, stockée et versionnée comme le reste du desk.
- **Worst-of :** par paramètre, le plus prudent. Avec des défauts signés : même signe, la plus grande valeur absolue ; signes opposés, à définir avec Luca (proposition : le plus défavorable sur ce produit, mesuré).
- **Pricer :** une section pilotée par le schéma (`schema.py`, `visible_if`, masques), qui montre le défaut et marque ce qui est surchargé. La taille vient de `general.notional`, donc une grille par devise tant qu'il n'y a pas de FX.
- **Valeurs initiales :** Luca n'en a pas donné. Mettre des valeurs de départ, étiquetées comme telles, et lui demander de les corriger.

## 6. Le lissage des digitales

Chaque digitale (barrière de coupon, déclencheur d'autocall, barrière finale de PDI, upside digital) devient un call ou put spread **du côté défavorable** :

```
largeur = max(min spread, saut de paiement / delta max)
```

- Le saut dépend de la digitale : le coupon pour une barrière de coupon, tout le capital perdu pour une PDI, l'écart entre valeur rappelée et valeur de continuation pour un autocall, qui dépend de la trajectoire. Choisir une définition simple et la documenter.
- **Le bon côté n'est pas toujours évident.** Sur un autocall, il dépend du produit. Proposition : essayer les deux côtés par digitale sur trajectoires communes et garder le défavorable.
- Dans le langage de script : un comparateur avec une rampe décalée (côté et largeur), en réutilisant les masques flottants de `_compare`. Une rampe symétrique reste celle des Greeks.
- **À définir avec Luca :** l'unité de `delta max` (% du nominal par 1 % de spot, ou devise) et le traitement d'une barrière quotidienne.

## 7. Les modèles

**Politique.** `RULES` devient une liste par famille : la base (toujours LV, réplication pour une européenne) et les modèles de provision. Point de départ à valider :

| Famille | Provisions de modèle |
|---|---|
| Européenne | Aucune |
| Barrière, path-dependent | SLV |
| Autocall | SLV |
| Worst-of | SLV panier, skew de corrélation |

« Positive » veut dire défavorable au desk, donc selon le signe de la position. Le mix SLV n'est pas calibré, et `mix_band` montre qu'il bouge plus un autocall que l'écart SLV - LV (`reports/replay.md`) : le fixer par groupe et l'exposer.
**Attention :** passer la base à LV change tous les devis, le Book et les chiffres des rapports. Le coupon SLV de la BRC de 2020 était 32 bp au-dessus du LV.

**SLV panier.** Le levier d'un nom ne dépend que de sa marginale, donc se calibre nom par nom et se met en cache par (nom, date). `simulate_basket` fait déjà avancer les variances, avec spot-vol par nom et résidus de vol indépendants entre noms. À documenter : la corrélation vol-vol qui en résulte. Tests : chaque nom reprice ses vanilles à 4 SE ; un panier de noms identiques à corrélation 1 redonne le mono-sous-jacent ; coût mesuré.

**Skew de corrélation.** Le rendre dynamique par une **corrélation locale** : `Z_i = sqrt(w) Z_0 + sqrt(1 - w) (L z)_i`, où `w(t, panier)` dépend du niveau du panier au début du pas.
- La matrice reste un mélange avec la matrice de 1, donc valide sur chaque trajectoire (comme `apply_premium`).
- Les incréments de chaque nom restent gaussiens de variance 1 conditionnellement au passé, donc **les marginales ne changent pas** : à vérifier par test, c'est tout l'intérêt.
- Calibrer `w` sur les CvC et PvP à 90, 100 et 110 %. **Seules des pseudo-cotations existent** (`basket_spreads`, `synthetic`, où la corrélation est une hypothèse) : sans vrais niveaux, cette provision reflète une hypothèse, et l'écran doit le dire. Demander à Luca des niveaux de broker, même indicatifs.
- Une CvC et une PvP sur un même strike sont le même instrument : le test doit le vérifier avec le modèle.
- Lire la littérature sur la corrélation locale avant de figer la forme de `w` (Langnau 2010, Guyon, Reghai : références à vérifier). Comparer au moins une alternative stochastique sur le coût et la qualité du fit, puis recommander.

## 8. Book et réserves

- Le trade stocke le détail figé de ses provisions, par ligne.
- Le Book valorise au prix de base en LV et montre la réserve restante à part, trade par trade et au total.
- **Amortissement :** linéaire sur la vie contractuelle, libération du reste au rappel ou à l'échéance, en proposition. À confirmer avec Luca (la vie attendue est une autre option).
- Le replay ajoute une ligne « libération de réserve » au P&L expliqué, et le P&L reste égal à valeur + cash au centime.
- Les trades existants n'ont pas de provisions : réserve nulle, signalée.

## 9. Plan, par incréments

Chaque étape finit par des tests verts, un commit local et une entrée de journal.

| Étape | Contenu | Fin d'étape |
|---|---|---|
| P0 | Lire, mesurer les temps, poser les questions du §10 en un seul message | Réponses de Luca |
| P1 | Contexte par nom dans le Pricer et le Book, depuis le lac (`basket_at`) | Fiche AAPL et worst-of sur leurs vraies surfaces |
| P2 | Moteur de bumps et récapitulatif, sur mono-sous-jacent | Coûts par ligne sur trajectoires communes, skew sans arbitrage |
| P3 | Groupes, paliers, schéma, surcharges, règle du plus prudent | Le Pricer charge les défauts d'une fiche |
| P4 | Base LV et provisions de modèle mono-sous-jacent | Autocall = LV + SLV si positif, rapports refaits |
| P5 | Lissage des digitales | Largeur et coût par digitale, delta borné mesuré |
| P6 | Réserves dans le trade et le Book, amortissement, replay | P&L égal à valeur + cash |
| P7 | SLV panier | Vanilles par nom à 4 SE, coût mesuré |
| P8 | Corrélation locale, calibration, provision, bump de skew | Marginales inchangées, CvC = PvP, CvC et PvP refittées |

P1 à P3 forment le noyau. P7 et P8 sont le plus risqué : prévoir de mesurer avant de figer.

**Critères de sortie.** Un devis d'autocall et de worst-of montre base, provisions de modèle, bumps, digitales et prix final, chaque ligne avec sa SE. Le trader surcharge un paramètre et voit l'effet. Une réserve suit un trade jusqu'à son rappel dans le replay. Le book de `scripts/seed_showcase.py` tourne, et son guide `reports/showcase.md` est mis à jour.

## 10. Questions à poser à Luca au démarrage, en un message

1. **Amortissement :** linéaire sur la vie contractuelle, ou sur la vie attendue ?
2. **Plus prudent et signes opposés** sur un worst-of : quelle règle ?
3. **Groupes, paliers et valeurs initiales :** lesquels, ou des valeurs de départ à corriger ?
4. **`delta max` :** quelle unité ? Et que faire d'une barrière quotidienne : décalage de barrière ?
5. **Interactions :** le prix final avec tous les bumps ensemble, et une ligne d'interaction, ça convient ?
6. **Le mix SLV :** un défaut par groupe ?
7. **CvC et PvP :** as-tu des niveaux de broker, même indicatifs, pour calibrer le skew de corrélation ?
8. **Les provisions de modèle** (§7) : la table par famille est-elle la bonne ? Faut-il aussi d'autres modèles (Heston seul, par exemple) ?

## 11. Garde-fous

- Rien de public : ni push ni PR. Commits locaux, en anglais, style `"Pricing: ..."`, sans lignes d'attribution (consigne de Luca).
- `data/` et le lac ne sont jamais versionnés. Les résultats contiennent des performances, pas de niveaux.
- Chiffres honnêtes : chaque prix avec sa SE, chaque temps mesuré, chaque valeur avec son `origin`. Une provision calibrée sur une hypothèse le dit à l'écran.
- Tests : chaque pricer stochastique contre une référence indépendante à 4 SE, seeds fixes.
- Les scripts se lancent par leur chemin, avec le venv partagé `C:\Users\Luca\Code\projets\option-pricing-lab\.venv`.
- Réponses à Luca en français ; code et rapports en anglais.
