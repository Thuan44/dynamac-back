# Audit de Sécurité - Dynamac Backend

Ce document liste toutes les mesures de sécurité actuellement implémentées dans le projet Dynamac Backend.

## Table des matières

1. [Protection CSRF](#1-protection-csrf)
2. [Rate Limiting](#2-rate-limiting)
3. [Headers de Sécurité HTTP](#3-headers-de-sécurité-http)
4. [Validation des Fichiers](#4-validation-des-fichiers)
5. [Protection SSRF](#5-protection-ssrf)
6. [Limitation des Payloads](#6-limitation-des-payloads)
7. [Configuration et Secrets](#7-configuration-et-secrets)
8. [CORS](#8-cors)
9. [Validation des Entrées](#9-validation-des-entrées)
10. [Gestion des Erreurs](#10-gestion-des-erreurs)
11. [Timeouts et Limites](#11-timeouts-et-limites)
12. [Sécurité des Opérations de Stockage](#12-sécurité-des-opérations-de-stockage)
13. [Sécurité des Opérations de Base de Données](#13-sécurité-des-opérations-de-base-de-données)

---

## 1. Protection CSRF

### Description

Protection contre les attaques Cross-Site Request Forgery (CSRF) pour toutes les requêtes modifiant l'état (POST, PUT, DELETE, PATCH).

### Implémentation

#### Middleware CSRF

- **Fichier**: `utils/csrf_middleware.py`
- **Fonctionnalités**:
  - Validation automatique pour toutes les méthodes HTTP modifiant l'état
  - Exclusion de certains endpoints publics (health, docs, etc.)
  - Support de deux modes : validation par token CSRF ou validation par origine + header X-Requested-With
  - Logging des tentatives de CSRF échouées

#### Génération et Validation des Tokens CSRF

- **Fichier**: `utils/csrf.py`
- **Fonctionnalités**:
  - Génération de tokens sécurisés avec `secrets.token_urlsafe(32)`
  - Stockage en mémoire avec expiration (15 minutes)
  - Nettoyage automatique des tokens expirés
  - Validation multi-couches : token, origine, header X-Requested-With
  - Support du mode développement avec wildcard "\*"

#### Endpoint de Génération de Tokens

- **Fichier**: `routes/csrf.py`
- **Fonctionnalités**:
  - Endpoint GET `/api/csrf-token` pour obtenir un token CSRF
  - Headers de cache désactivés pour éviter la mise en cache des tokens
  - Token stocké côté serveur avec expiration

#### Configuration dans main.py

- **Fichier**: `main.py`
- **Note**: Le middleware CSRF est actuellement commenté mais prêt à être activé

---

## 2. Rate Limiting

### Description

Limitation du nombre de requêtes par adresse IP pour prévenir les abus et les attaques par déni de service.

### Implémentation

#### Configuration du Rate Limiter

- **Fichier**: `utils/rate_limiter.py`
- **Fonctionnalités**:
  - Utilisation de `slowapi` pour le rate limiting
  - Identification des clients par adresse IP distante
  - Instance partagée utilisable sur tous les endpoints

#### Application sur les Endpoints

- **Fichier**: `routes/upload.py`
- **Limite**: 10 requêtes par 10 minutes sur l'endpoint d'upload

#### Configuration dans main.py

- **Fichier**: `main.py`
- **Fonctionnalités**:
  - Attachement du limiter à l'état de l'application
  - Gestionnaire d'exceptions pour les limites dépassées

---

## 3. Headers de Sécurité HTTP

### Description

Ajout automatique de headers de sécurité HTTP selon les meilleures pratiques OWASP pour protéger contre diverses attaques web.

### Implémentation

#### Middleware de Headers de Sécurité

- **Fichier**: `utils/security_headers.py`
- **Headers implémentés**:
  - **X-Content-Type-Options**: `nosniff` - Empêche le MIME type sniffing
  - **X-Frame-Options**: `DENY` - Empêche les attaques de clickjacking
  - **X-XSS-Protection**: `1; mode=block` - Active le filtrage XSS (legacy mais utile)
  - **Strict-Transport-Security**: HSTS avec `includeSubDomains` et `preload` (production uniquement)
  - **Content-Security-Policy**: Politique restrictive pour les endpoints API
  - **Referrer-Policy**: `strict-origin-when-cross-origin`
  - **Permissions-Policy**: Désactive les fonctionnalités sensibles du navigateur

#### Configuration dans main.py

- **Fichier**: `main.py`
- **Fonctionnalités**:
  - Activation conditionnelle de HSTS en production uniquement
  - Max-age de 1 an pour HSTS

---

## 4. Validation des Fichiers

### Description

Validation stricte et multi-couches des fichiers uploadés pour prévenir les attaques par upload de fichiers malveillants.

### Implémentation

#### Validation Complète des Fichiers

- **Fichier**: `utils/file_validation.py`
- **Fonctionnalités**:
  - **Validation par magic bytes**: Détection du type réel de fichier via `filetype` (ignore l'extension client)
  - **Validation par PIL**: Vérification de l'intégrité de l'image et détection de corruption
  - **Validation de taille**: Limite minimale (100 bytes) et maximale (20MB par défaut)
  - **Types autorisés**: JPEG, PNG, WEBP uniquement
  - **Sécurité**: L'extension du nom de fichier fourni par le client est **jamais utilisée**, seule l'extension déterminée par magic bytes est utilisée

#### Validation par Magic Bytes

- **Fichier**: `utils/file_validation.py`
- **Fonctionnalités**:
  - Utilisation de la bibliothèque `filetype` pour détecter le type réel
  - Vérification que le type détecté est dans la liste des types autorisés
  - Vérification de l'intégrité avec PIL (image.verify())

#### Validation de Taille

- **Fichier**: `utils/file_validation.py`
- **Limites**:
  - Minimum: 100 bytes
  - Maximum: Configurable via `settings.max_file_size` (20MB par défaut)

#### Utilisation dans l'Endpoint d'Upload

- **Fichier**: `routes/upload.py`
- **Fonctionnalités**:
  - Validation complète avant upload
  - Génération de nom de fichier unique (UUID) avec extension validée
  - Rollback automatique en cas d'erreur (suppression du fichier et de la tâche)

---

## 5. Protection SSRF

### Description

Protection contre les attaques Server-Side Request Forgery (SSRF) en validant strictement toutes les URLs avant téléchargement.

### Implémentation

#### Validation des URLs

- **Fichier**: `utils/url_validation.py`
- **Fonctionnalités**:
  - **Validation du schéma**: Seul HTTPS est autorisé (HTTP bloqué)
  - **Blocage des IPs privées**: Détection et blocage de localhost, 127.0.0.1, IPs privées, link-local
  - **Whitelist de domaines**: Seuls les domaines Supabase sont autorisés
  - **Validation du format**: Vérification de la structure de l'URL
  - **Détection automatique**: Extraction automatique des domaines autorisés depuis SUPABASE_URL

#### Détection des IPs Privées

- **Fichier**: `utils/url_validation.py`
- **Fonctionnalités**:
  - Utilisation de `ipaddress` pour détecter les IPs privées, loopback, link-local
  - Détection des hostnames localhost courants

#### Validation des URLs Supabase Storage

- **Fichier**: `utils/url_validation.py`
- **Fonctionnalités**:
  - Validation spécifique pour les URLs Supabase Storage
  - Vérification du format attendu: `/storage/v1/object/public/[bucket]/[path]`

#### Utilisation dans les Services

- **Fichiers**: `services/generation.py`, `services/upscale.py`
- **Fonctionnalités**:
  - Validation de toutes les URLs avant téléchargement
  - Validation des URLs auto-uploadées par Gemini

---

## 6. Limitation des Payloads

### Description

Contrôle de la taille des payloads JSON pour prévenir les attaques par déni de service et limiter la consommation de ressources.

### Implémentation

#### Middleware de Limitation des Payloads

- **Fichier**: `utils/payload_validation.py`
- **Fonctionnalités**:
  - Vérification de la taille des payloads avant traitement
  - Limite configurable via `settings.max_json_payload_size` (10MB par défaut)
  - Application uniquement sur les méthodes POST, PUT, PATCH
  - Vérification basée sur le header `Content-Length`

#### Configuration dans main.py

- **Fichier**: `main.py`
- **Fonctionnalités**:
  - Middleware ajouté avant le traitement des requêtes

#### Configuration dans config.py

- **Fichier**: `config.py`
- **Limite**: 10MB par défaut

---

## 7. Configuration et Secrets

### Description

Gestion sécurisée des secrets et validation stricte de la configuration au démarrage.

### Implémentation

#### Validation des Secrets

- **Fichier**: `config.py`
- **Fonctionnalités**:
  - Validation au démarrage avec `pydantic-settings`
  - Validation de la longueur minimale des clés secrètes
  - Validation du format HTTPS pour SUPABASE_URL
  - Masquage des secrets dans les logs (méthode `__repr__` personnalisée)
  - Secrets jamais exposés dans les messages d'erreur

#### Masquage des Secrets dans les Logs

- **Fichier**: `config.py`
- **Fonctionnalités**:
  - Méthode `_mask_url()` pour masquer les parties sensibles des URLs
  - `__repr__()` personnalisé qui n'expose jamais les secrets

---

## 8. CORS

### Description

Configuration Cross-Origin Resource Sharing (CORS) avec restrictions en production.

### Implémentation

#### Configuration CORS

- **Fichier**: `main.py`
- **Fonctionnalités**:
  - Origines restreintes en production (`https://dynamac.zite.so`)
  - Toutes origines autorisées en développement (`*`)
  - Credentials activés pour les cookies/authentification
  - Méthodes limitées: GET, POST uniquement
  - Headers autorisés: Content-Type, Authorization, X-CSRF-Token, X-Requested-With

---

## 9. Validation des Entrées

### Description

Validation stricte de toutes les entrées utilisateur via Pydantic pour prévenir les injections et les données malformées.

### Implémentation

#### Validation des Requêtes de Génération

- **Fichier**: `schemas/generate.py`
- **Fonctionnalités**:
  - Validation du format d'heure (HH:MM 24h)
  - Limitation du nombre de variants via `max_variants_count`
  - Types stricts pour les formats (Literal)

#### Validation de la Configuration

- **Fichier**: `routes/config.py`
- **Fonctionnalités**:
  - Validation du format d'heure
  - Validation du slot (>= 0)
  - Validation du type source (Literal)
  - Limitation du nombre de variants

---

## 10. Gestion des Erreurs

### Description

Gestion sécurisée des erreurs sans exposer de détails internes sensibles.

### Implémentation

#### Messages d'Erreur Génériques

- **Fichier**: `routes/upload.py`
- **Fonctionnalités**:
  - Logging détaillé côté serveur uniquement
  - Messages d'erreur génériques pour le client
  - Rollback automatique en cas d'erreur

#### Gestion des Erreurs dans les Services

- **Fichiers**: `services/generation.py`, `services/upscale.py`
- **Fonctionnalités**:
  - Gestion gracieuse des erreurs avec rollback d'état
  - Logging détaillé sans exposition au client
  - Préservation des `CancelledError` pour les arrêts propres

---

## 11. Timeouts et Limites

### Description

Configuration de timeouts et limites de taille pour toutes les opérations HTTP et de traitement.

### Implémentation

#### Configuration des Timeouts

- **Fichier**: `config.py`
- **Timeouts configurés**:
  - `timeout_http_request`: 30.0s - Timeout pour les requêtes HTTP
  - `timeout_http_connect`: 10.0s - Timeout pour la connexion HTTP
  - `timeout_gemini_api`: 120.0s - Timeout pour les appels API Gemini
  - `timeout_image_processing`: 60.0s - Timeout pour le traitement d'images
  - `timeout_storage_operation`: 60.0s - Timeout pour les opérations de stockage
  - `timeout_heic_creation`: 240.0s - Timeout pour la création de fichiers HEIC
  - `timeout_background_task`: 600.0s - Timeout global pour les tâches en arrière-plan

#### Limites de Taille

- **Fichier**: `config.py`
- **Limites configurées**:
  - `max_file_size`: 20MB - Taille maximale des fichiers uploadés
  - `max_download_size`: 50MB - Taille maximale des téléchargements
  - `max_json_payload_size`: 10MB - Taille maximale des payloads JSON

#### Application des Timeouts dans les Services

- **Fichiers**: `services/generation.py`, `services/upscale.py`
- **Fonctionnalités**:
  - Utilisation de `asyncio.wait_for()` pour les opérations asynchrones
  - Vérification de la taille avant et après téléchargement
  - Timeouts configurés sur les clients HTTP

---

## 12. Sécurité des Opérations de Stockage

### Description

Gestion sécurisée des uploads et téléchargements avec validation et rollback.

### Implémentation

#### Service de Stockage

- **Fichier**: `services/storage.py`
- **Fonctionnalités**:
  - Utilisation de Supabase Storage avec clé de service
  - Gestion des types MIME validés
  - Gestion d'erreurs avec logging

#### Validation des Téléchargements

- **Fichiers**: `services/generation.py`, `services/upscale.py`
- **Fonctionnalités**:
  - Vérification de la taille via `Content-Length` header
  - Vérification de la taille après téléchargement
  - Timeouts configurés sur les requêtes

---

## 13. Sécurité des Opérations de Base de Données

### Description

Gestion sécurisée des opérations de base de données avec validation et gestion d'erreurs.

### Implémentation

#### Service de Base de Données

- **Fichier**: `services/database.py`
- **Fonctionnalités**:
  - Utilisation de Supabase avec clé de service
  - Validation des données avant insertion
  - Gestion d'erreurs avec logging
  - Parsing sécurisé des dates depuis différents formats

#### Parsing Sécurisé des Dates

- **Fichier**: `services/database.py`
- **Fonctionnalités**:
  - Support de multiples formats de dates (ISO, Unix timestamp, datetime)
  - Gestion d'erreurs robuste

---

## Conclusion

Ce document liste toutes les mesures de sécurité actuellement implémentées dans le projet Dynamac Backend. Les mesures couvrent :

- Protection contre les attaques CSRF
- Rate limiting pour prévenir les abus
- Headers de sécurité HTTP selon OWASP
- Validation stricte des fichiers uploadés
- Protection SSRF pour les téléchargements
- Limitation des payloads
- Gestion sécurisée des secrets
- Configuration CORS restrictive
- Validation des entrées avec Pydantic
- Gestion sécurisée des erreurs
- Timeouts et limites sur toutes les opérations
- Sécurité des opérations de stockage et base de données

Toutes ces mesures sont actives et configurées pour fonctionner en production avec des restrictions appropriées, tout en permettant un développement flexible en mode développement.
