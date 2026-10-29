# Déploiement sur Oracle Cloud Free Tier (gratuit à vie, non-US)

Remplace le VPS GCP (fermé suite à l'arrêt de l'abonnement Google One). Mêmes 4 services
Docker Compose + nginx qu'avant, migrés vers Oracle Cloud parce que son "Always Free" tier
est gratuit en permanence (pas un essai limité dans le temps) et permet de choisir une région
hors des États-Unis -- indispensable puisque Binance bloque les requêtes depuis les IP
cloud américaines (déjà rencontré avec GCP US, et confirmé à nouveau avec les runners GitHub
Actions eux-mêmes, hébergés aux US).

## Étape 1 : créer le compte (toi, pas moi)

1. Va sur https://signup.oraclecloud.com
2. Email + mot de passe, puis vérification par email.
3. **Choix de la "Home Region" : CRITIQUE, impossible à changer après coup.** Choisis une
   région non-US. Recommandé : **Germany Central (Frankfurt)** ou **France Central (Paris)**
   si disponible pour les nouveaux comptes -- proches, latence correcte vers Binance.
4. Informations d'identité + carte bancaire. La carte sert uniquement à vérifier que tu es
   une vraie personne (anti-abus) -- **tu ne seras jamais facturé tant que tu restes dans le
   "Always Free" tier**, qui n'expire jamais (contrairement à l'essai gratuit à durée limitée
   que propose aussi Oracle, qu'il ne faut pas confondre).

## Étape 2 : créer la VM gratuite

Une fois connecté à la console OCI :

1. Menu ☰ → **Compute** → **Instances** → **Create instance**
2. Nom : `patternpulse-vm`
3. **Image** : Ubuntu (dernière LTS, ex. 24.04) -- pas Oracle Linux.
4. **Shape** : clique "Change shape" → onglet **Ampere** → `VM.Standard.A1.Flex` → configure
   **2 OCPU / 12 GB RAM** (reste dans l'enveloppe "Always Free" de 4 OCPU/24 GB au total, et
   donne bien plus de marge que l'option AMD gratuite à 1 GB RAM, nécessaire ici pour les 4
   processus Python + LightGBM + Streamlit en parallèle).
5. **Add SSH keys** → "Paste public keys" → colle exactement ceci :
   ```
   ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJTVqh8DuIobc0oACdDjG1ct8GbgOd6KioYOum+LW6jM patternpulse-oracle-vm
   ```
6. **Create**. Attends que l'état passe à "Running" (1-2 minutes), note l'**adresse IP
   publique** affichée sur la page de l'instance.

## Étape 3 : ouvrir les ports (console OCI, pas l'OS)

Oracle filtre le trafic à deux niveaux : la "Security List" du réseau (console web,
obligatoire) ET le pare-feu Ubuntu (je m'en occupe par SSH). Pour le niveau réseau :

1. Sur la page de l'instance, clique le lien du **subnet** (sous "Primary VNIC").
2. Clique la **Security List** par défaut associée.
3. **Add Ingress Rules** :
   - Source CIDR `0.0.0.0/0`, protocole TCP, port de destination `80` (dashboard)
   - Source CIDR `0.0.0.0/0`, protocole TCP, port de destination `22` (déjà présent par défaut normalement -- vérifie juste qu'il y est)

## Étape 4 : donne-moi juste l'IP publique

Une fois la VM "Running" et les règles ajoutées, donne-moi l'adresse IP publique -- je me
connecte directement en SSH avec la clé déjà générée et je fais tout le reste (Docker,
déploiement des 4 services, vérification, ouverture du pare-feu OS-level, configuration du
hostname nip.io) exactement comme pour l'ancien VPS GCP.
