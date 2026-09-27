# ArchivIA - Vision produit

## Objectif

Transformer des PDF natifs ou scannes en fichiers Excel clairs, structurés et faciles a exploiter.

L'outil doit aider les utilisateurs qui recoivent des documents PDF contenant des tableaux, listes, releves, formulaires ou rapports, et qui veulent les convertir rapidement en donnees manipulables.

## Priorite

La priorite est la recuperation intelligente des tableaux :

1. Detecter les pages exploitables.
2. Extraire le texte natif quand il existe.
3. Utiliser l'OCR sur les pages scannees.
4. Identifier les lignes qui ressemblent a des tableaux.
5. Regrouper les lignes en blocs coherents.
6. Exporter vers Excel avec une presentation lisible.
7. Signaler clairement les zones incertaines.

## Principe de qualite

L'outil ne doit pas faire semblant d'etre certain. Lorsqu'une page est scannee, vide, mal extraite ou ambigue, l'information doit apparaitre dans `Controle_qualite`.

## Evolution

Les futures ameliorations possibles sont :

- detection visuelle des bordures ;
- reconstruction de cellules fusionnees ;
- detection des en-tetes multi-lignes ;
- nettoyage metier des dates, montants et identifiants ;
- interface graphique simple ;
- traitement par lot de plusieurs PDF.
