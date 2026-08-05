# Suivi des modifications

## 2026-08-05 - Ponctuation de la dictee continue

- [x] Re-decoder une fenetre de plusieurs phrases a chaque vraie pause, pour
      rendre au modele le contexte que le decoupage lui retirait.
- [x] Refuser un polissage qui derape (sortie vide, boucle) et dedoublonner les
      coutures entre fenetres — `polish.py`, fonctions pures.
- [x] Ne pas re-decoder une fenetre d'une seule phrase : meme audio, meme texte.
- [x] Ajouter l'apercu grise de la phrase en cours, qui saute son tour plutot
      que de retarder une phrase, et reste inactif sans carte graphique.
- [x] N'ecrire au curseur que du texte poli, pour n'avoir jamais a revenir en
      arriere dans le document.
- [x] Corriger la derniere phrase jamais frappee au curseur : l'overlay la
      jetait, l'etat etant deja passe a « transcribing ».
- [x] Verifier qu'aucun echantillon ne part deux fois au moteur, y compris apres
      une coupe a `max_phrase_s`.

## 2026-08-01 - Interface Murmure

- [x] Verifier le suivi Git et identifier les fichiers ignores volontairement.
- [x] Restaurer le clic gauche sur l'icone Murmure de la zone de notification.
- [x] Ajouter des icones plus parlantes aux entrees principales de la fenetre.
- [x] Retirer le libelle Murmure redondant et son icone de la barre laterale.
- [x] Ajuster subtilement le theme vers rose/magenta et bleu violace.
- [x] Appliquer un veritable fond acrylique Windows avec une teinte sombre lisible.
- [x] Ajouter des reglages d'apparence persistants pour la palette et la densite du fond.
- [x] Ajouter une confirmation avant la suppression d'une dictee.
- [x] Afficher les statistiques de traitement dans l'onglet Historique.
- [x] Integrer les lecteurs audio et l'ouverture du dossier des enregistrements.
- [x] Harmoniser le fond de la fenetre avec la barre de titre Windows.
- [x] Classer les modeles du plus puissant au plus leger.
- [x] Reordonner la navigation : Dicter, Fichiers, Modeles, Historique, Reglages.
- [x] Autoriser le rendu des icones de navigation dans la politique Tauri.
- [x] Remplacer les statistiques persistantes de bas de barre par un etat plus utile.
- [x] Compiler et lancer la version Tauri mise a jour.
