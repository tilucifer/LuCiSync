# Synctool

Application graphique pour synchroniser des fichiers et dossiers vers un dossier local, un partage réseau ou un chemin WSL. Elle fonctionne sous Linux et Windows.

## Installation

Python 3.10 ou plus récent est requis.

```sh
python -m venv .venv
# Linux
. .venv/bin/activate
# Windows PowerShell : .venv\Scripts\Activate.ps1
python -m pip install .
synctool
```

Sous Linux, `rsync` est utilisé s’il est installé. Sous Windows, Synctool utilise `robocopy`, fourni avec Windows. Si `rsync` n’est pas disponible, le moteur Python compare tailles et dates, copie seulement les fichiers modifiés et peut reprendre un fichier interrompu.

## Utilisation

- Ajoutez des fichiers ou dossiers avec les boutons, ou déposez des fichiers depuis votre gestionnaire de fichiers.
- **Fichiers .env** recherche récursivement les fichiers `.env` et `.env.*` sous le dossier choisi. Les dossiers `.git`, `node_modules`, `.venv`, `venv`, `__pycache__` et `.cache` sont ignorés.
- Double-cliquez sur le chemin cible d’une ligne pour le modifier. Glissez les lignes pour les réordonner et utilisez `×` pour en retirer une.
- Choisissez la destination avec **Parcourir…**. **Emplacements détectés** propose les lecteurs réseau montés et, sous Windows, les distributions WSL détectées par `wsl.exe`. Les chemins UNC peuvent aussi être saisis directement.
- Enregistrez plusieurs configurations JSON avec **Enregistrer sous…**, puis rechargez-les avec **Charger**. La configuration active est enregistrée automatiquement.
- La table affiche le résultat de chaque source. Les dernières opérations sont aussi inscrites dans un journal JSONL local.

La synchronisation est à sens unique : elle copie ou met à jour les fichiers dans la destination et ne supprime rien. Si plusieurs sources ciblent le même chemin, la synchronisation est bloquée jusqu’à ce que les chemins soient corrigés. Les configurations et le journal contiennent les chemins et résultats, jamais le contenu des fichiers.

## Fichiers locaux

- Configuration : `%APPDATA%\Synctool\config.json` sous Windows, ou `$XDG_CONFIG_HOME/synctool/config.json` sous Linux (par défaut `~/.config/synctool/config.json`).
- Journal : `sync-history.jsonl` dans le même dossier.
