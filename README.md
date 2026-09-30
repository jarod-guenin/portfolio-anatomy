# Portfolio — explorateur de dessins

Site statique façon explorateur Windows : un dossier par dessin, une fiche technique complète (30 métriques) et une protection des images.

```
portfolio/
├── analyse.py        ← calcule les stats + génère les images web
├── calques.py        ← lit les calques des .tif / .psd
├── config.json       ← ton nom, filigrane, page d'accueil, taille des images
├── accueil/          ← TON IMAGE D'ACCUEIL (originale, jamais mise en ligne)
├── requirements.txt
├── dessins/          ← TES ORIGINAUX (jamais mis en ligne)
│   └── Griffith/
│       ├── griffith.png
│       └── info.json ← titre, description, tags, catégorie, date
└── docs/             ← le site publié
    ├── index.html, style.css, app.js, robots.txt
    ├── data.json     ← généré
    ├── img/          ← versions web filigranées (générées)
    └── thumb/        ← miniatures (générées)
```

## 1. Installation (une seule fois)

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux : source .venv/bin/activate)
pip install -r requirements.txt
```

Puis ouvre `config.json` et remplace **Ton Nom**, le texte du filigrane et l'URL de ton site.

## 2. Ajouter un dessin

1. Crée un dossier dans `dessins/` (son nom = nom par défaut du dessin), mets-y **une seule** image (PNG, JPG, WebP, TIFF…).
2. Lance :
   ```bash
   python analyse.py
   ```
3. Le script crée un `info.json` dans le dossier : complète-le, puis relance `python analyse.py`.

```json
{
  "titre": "Griffith",
  "description": "Griffith sous un ciel rouge sang.\nPeint en 6 heures sur Krita.",
  "tags": ["Berserk", "Digital"],
  "categorie": "Fan art",
  "date": "2025-03-12"
}
```

- Les catégories apparaissent automatiquement dans la barre latérale.
- Modifier / renommer / supprimer un dessin : change le dossier, relance le script.
- Seuls les dessins nouveaux ou modifiés sont réanalysés (cache dans `.cache/`).
- Tu as changé le filigrane ou `config.json` ? → `python analyse.py --force`

## Page d'accueil « papier déchiré »

Au lancement du site, ton dessin d'accueil couvre tout l'écran. Le visiteur **attrape le bord du bas et tire vers le haut** (ou le bord du haut et tire vers le bas) : la page se déchire en suivant le doigt ou la souris. Au-delà de 40 % de la hauteur, elle se déchire entièrement et les deux moitiés tombent pour révéler le bureau. En dessous, le papier se referme.

1. Mets ton image dans le dossier `accueil/` (n'importe quel nom : la première image trouvée est utilisée ; ou indique le chemin exact dans `config.json` → `"accueil"`).
2. Relance `python analyse.py` → elle est convertie en `docs/accueil.webp`.

Réglages dans `config.json` :
- `"accueil_ajustement"` : `"cover"` (remplit l'écran, rogne les bords) ou `"contain"` (image entière, bandes de couleur autour) ;
- `"accueil_fond"` : couleur autour de l'image en mode `contain` ;
- `"accueil_texte"` : le petit texte d'aide en bas ;
- `"accueil": ""` pour désactiver la page d'accueil.

Accessibilité : bouton **Entrer** en bas à droite et touche Entrée ; si le visiteur a demandé moins d'animations dans son système, la page s'efface simplement.
Un lien direct vers un dessin (`…/#/d/xxxx`) saute la page d'accueil.

## Calques (.tif / .psd)

Si le dessin est un **.tif enregistré avec ses calques** (ou un **.psd**), le script lit ses calques et le site affiche une **fenêtre Calques** dans chaque dossier :

- liste défilante, du haut vers le bas comme dans ton logiciel : œil (visible / masqué), vignette, mode de fusion, nom, opacité, groupes repliables ;
- clic sur un calque → son cadre s'affiche sur le dessin + détails (taille, position, pixels peints, couverture, couleur moyenne, écrêtage, verrou) ;
- fenêtre déplaçable (barre de titre), redimensionnable (coin bas-droit), fermable (✕) et réouvrable à tout moment (bouton **▤ Calques** ou touche **C**) ;
- section **G. Calques** dans la fiche : nombre de calques, groupes, masqués, modes de fusion, couverture par calque.

Pour vérifier ce que le script trouve dans un fichier :

```bash
python analyse.py --inspect "dessins/Griffith/griffith.tif"
```

Formats lus : **TIFF multicalque SketchBook** (vérifié sur un fichier SketchBook Pro 8.8.5), TIFF avec calques Photoshop (tag 37724, écrit par Photoshop, Krita, Clip Studio, Affinity, Photopea…), PSD, TIFF multipage.
SketchBook : nom, visibilité, opacité et modes Normal / Multiplier sont lus ; les autres modes de fusion s'affichent « Mode n°X » et les groupes ne sont pas encore reconnus (envoie-moi un fichier qui en contient pour les ajouter). Un fichier aplati affiche « Image aplatie ».
Dans un dossier de dessin, deux possibilités :
- **seulement le .tif** : il sert à tout (image affichée, stats et calques) ;
- **une image exportée (.png / .jpg) + le .tif** : l'export est affiché et analysé, le .tif ne sert qu'à la fenêtre Calques.
Pour ne pas publier les vignettes de calques : `"calques_vignettes": false` dans `config.json`.

## 3. Voir le site en local

Le site doit être servi (pas ouvert en double-clic) :

```bash
python -m http.server 8000 -d docs
```

Puis ouvre http://localhost:8000

## 4. Mettre en ligne (GitHub Pages, gratuit)

1. Crée un dépôt **public** sur GitHub (ex. `portfolio`, ou `tonpseudo.github.io` pour avoir l'adresse courte).
2. Dans le dossier du projet :
   ```bash
   git init
   git add .
   git commit -m "Premier dépôt"
   git branch -M main
   git remote add origin https://github.com/tonpseudo/portfolio.git
   git push -u origin main
   ```
3. Sur GitHub : **Settings → Pages → Build and deployment → Deploy from a branch → `main` / `/docs`** → Save.
4. Environ une minute plus tard, le site est en ligne.

**Ensuite, à chaque nouveau dessin :** `python analyse.py`, puis `git add . && git commit -m "Nouveau dessin" && git push`.

> Le `.gitignore` empêche l'envoi des originaux : seuls `info.json` et les versions web filigranées partent sur GitHub. Vérifie avec `git status` avant ton premier push.

## Protection des dessins

| Mesure | Où |
|---|---|
| Originaux jamais publiés, versions web réduites (1600 px) | `analyse.py`, `.gitignore` |
| Filigrane : `"discret"` (mentions petites et espacées, par défaut), `"dense"` (mosaïque) ou `"coin"` (signature en bas à droite) | `config.json` → `filigrane`, `filigrane_style`, `filigrane_opacite` |
| Copyright dans les métadonnées EXIF (Artist, Copyright) | `analyse.py` |
| Empreinte SHA-256 de l'original publiée + historique Git daté | fiche du dessin |
| Image dessinée dans un `<canvas>`, clic droit et glisser bloqués | `app.js` |
| Robots d'IA bloqués (`robots.txt` + balise `noai`) | `docs/` |
| Page « Droits & mentions » | dans le site |

Aucune protection n'empêche une capture d'écran : ces mesures rendent la copie pénible et te permettent de prouver ta paternité. Pour aller plus loin contre l'entraînement d'IA, passe tes images dans **Glaze** avant de lancer le script.

## Métriques calculées

- **A. Fichier** : dimensions, mégapixels, ratio, format, poids, bits/pixel, taux de compression, profondeur, canaux, profil ICC, DPI, transparence, SHA-256
- **B. Couleur** : palette k-means (Lab), couleurs uniques, saturation, colorfulness de Hasler-Süsstrunk, roue chromatique, température (McCamy), harmonie, couleurs moyenne/médiane
- **C. Lumière** : histogrammes luminance et R/G/B, moyenne, médiane, contraste RMS, plage P1–P99, noirs bouchés, blancs cramés, clé tonale
- **D. Texture** : densité de contours (Canny), netteté (laplacien), entropie de Shannon, orientation des traits, dimension fractale
- **E. Composition** : centre de masse, écart aux tiers, carte de saillance (spectral residual), poids par quadrant, symétrie
- **F. Portfolio** : rangs du dessin, 3 dessins les plus proches (ΔE entre palettes)
- **G. Calques** : nombre de calques / groupes / masqués / vides, écrêtage, opacités, modes de fusion, imbrication, couverture du canevas par calque, poids des calques dans le fichier
