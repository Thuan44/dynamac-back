# Audit de Sécurité - Dynamac Backend

**Date de l'audit:** Décembre 2025  
**Version de l'application:** 1.0.0  
**Auditeur:** Audit de sécurité automatisé

## Résumé Exécutif

Cet audit de sécurité identifie les vulnérabilités et risques de sécurité dans l'application backend Dynamac. L'application est une API FastAPI qui gère l'upload, la génération et le traitement d'images pour créer des fonds d'écran dynamiques macOS.

### Niveau de Risque Global: **ÉLEVÉ** 🔴

Plusieurs vulnérabilités critiques ont été identifiées, notamment l'absence d'authentification/autorisation, une validation de fichiers insuffisante, et des configurations CORS trop permissives.

---

## 1. Vulnérabilités Critiques 🔴

### 1.1 Absence d'Authentification et d'Autorisation

**Sévérité:** Critique  
**Fichiers concernés:** Tous les fichiers de routes (`routes/*.py`)

**Description:**
Toutes les routes de l'API sont publiques et accessibles sans authentification. N'importe qui peut :

- Uploader des fichiers
- Accéder aux statuts des tâches
- Configurer des tâches
- Générer des images (consommant des ressources API Gemini)
- Upscaler des images

**Impact:**

- Consommation abusive de ressources (API Gemini, stockage Supabase)
- Coûts financiers non contrôlés
- Accès non autorisé aux données utilisateurs
- Risque d'abus (DoS, spam)

**Recommandations:**

1. Implémenter un système d'authentification (JWT, OAuth2, ou API keys)
2. Ajouter des middlewares d'authentification sur toutes les routes
3. Implémenter un système d'autorisation basé sur les rôles si nécessaire
4. Considérer l'utilisation de FastAPI Security (OAuth2PasswordBearer)

**Exemple de correction:**

```python
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    # Valider le token (JWT, API key, etc.)
    if not is_valid_token(token):
        raise HTTPException(status_code=401, detail="Invalid authentication")
    return token

@router.post("/upload")
async def upload_image(
    file: UploadFile = File(...),
    token: str = Depends(verify_token)
):
    # ... code existant
```

---

### 1.2 Validation de Fichiers Insuffisante

**Sévérité:** Critique  
**Fichier concerné:** `routes/upload.py`

**Description:**
La validation des fichiers uploadés se base uniquement sur le `content_type` fourni par le client, qui peut être falsifié. Il n'y a pas de vérification réelle du contenu du fichier (magic bytes, signature de fichier).

**Impact:**

- Upload possible de fichiers malveillants (exécutables, scripts)
- Risque d'exploitation de vulnérabilités dans le traitement d'images
- Stockage de fichiers non conformes

**Recommandations:**

1. Valider les fichiers en vérifiant les magic bytes/signatures
2. Utiliser `python-magic` ou `filetype` pour identifier le type réel
3. Vérifier que le fichier est bien une image valide avec PIL avant traitement
4. Scanner les fichiers pour détecter les contenus malveillants

**Exemple de correction:**

```python
import filetype
from PIL import Image
from io import BytesIO

# Après avoir lu file_content
# Vérifier les magic bytes
kind = filetype.guess(file_content)
if not kind or kind.mime not in settings.allowed_image_types:
    raise HTTPException(status_code=400, detail="Invalid file type")

# Vérifier que c'est une image valide
try:
    image = Image.open(BytesIO(file_content))
    image.verify()  # Vérifie l'intégrité
except Exception:
    raise HTTPException(status_code=400, detail="Invalid image file")
```

---

### 1.3 Validation d'URL Source Manquante

**Sévérité:** Critique  
**Fichiers concernés:** `services/generation.py`, `services/upscale.py`

**Description:**
L'application télécharge des images depuis n'importe quelle URL fournie sans validation. Cela peut permettre :

- SSRF (Server-Side Request Forgery)
- Accès à des ressources internes (localhost, réseaux privés)
- Consommation excessive de bande passante

**Impact:**

- Exposition de services internes
- Attaques SSRF contre des services locaux
- Consommation abusive de ressources réseau

**Recommandations:**

1. Valider que l'URL pointe vers un domaine autorisé (whitelist)
2. Bloquer les URLs pointant vers localhost/privé (127.0.0.1, 10.x.x.x, 192.168.x.x, etc.)
3. Limiter la taille des réponses HTTP téléchargées
4. Implémenter un timeout strict sur les requêtes HTTP

**Exemple de correction:**

```python
from urllib.parse import urlparse
import ipaddress

ALLOWED_DOMAINS = ["supabase.co", "storage.googleapis.com"]  # Domaines autorisés

def validate_url(url: str) -> bool:
    parsed = urlparse(url)

    # Bloquer les schémas non autorisés
    if parsed.scheme not in ["http", "https"]:
        return False

    # Bloquer les IPs privées/localhost
    try:
        hostname = parsed.hostname
        if hostname:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback:
                return False
    except ValueError:
        # C'est un hostname, vérifier le domaine
        if not any(hostname.endswith(domain) for domain in ALLOWED_DOMAINS):
            return False

    return True
```

---

## 2. Vulnérabilités Élevées 🟠

### 2.1 Configuration CORS Trop Permissive

**Sévérité:** Élevée  
**Fichier concerné:** `main.py`

**Description:**
La configuration CORS utilise `allow_methods=["*"]` et `allow_headers=["*"]`, ce qui est trop permissif même si l'origine est restreinte.

**Impact:**

- Risque d'exploitation si l'origine autorisée est compromise
- Exposition à des attaques CSRF si combiné avec d'autres vulnérabilités

**Recommandations:**

1. Limiter les méthodes HTTP aux seules nécessaires (GET, POST)
2. Limiter les headers autorisés aux seuls nécessaires
3. Ajouter des headers de sécurité supplémentaires (X-Content-Type-Options, etc.)

**Exemple de correction:**

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://dynamac.zite.so"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],  # Seulement les méthodes nécessaires
    allow_headers=["Content-Type", "Authorization"],  # Headers spécifiques
    expose_headers=["X-Request-ID"],
    max_age=3600,
)
```

---

### 2.2 Absence de Rate Limiting

**Sévérité:** Élevée  
**Fichiers concernés:** Toutes les routes

**Description:**
Aucun mécanisme de rate limiting n'est implémenté, permettant des attaques par déni de service (DoS) ou abus de ressources.

**Impact:**

- Attaques DoS faciles
- Consommation excessive de ressources (API Gemini, stockage)
- Coûts financiers non contrôlés
- Dégradation des performances pour les utilisateurs légitimes

**Recommandations:**

1. Implémenter un rate limiting par IP et/ou par utilisateur
2. Utiliser `slowapi` ou `fastapi-limiter` avec Redis
3. Configurer des limites différentes par endpoint (upload plus restrictif)
4. Implémenter un système de quotas

**Exemple de correction:**

```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@router.post("/upload")
@limiter.limit("10/minute")  # 10 uploads par minute
async def upload_image(...):
    # ... code existant
```

---

### 2.3 Absence de Validation de Taille de Payload JSON

**Sévérité:** Élevée  
**Fichiers concernés:** `routes/config.py`, `routes/generate.py`

**Description:**
Aucune limite n'est imposée sur la taille des payloads JSON, permettant des attaques par payloads volumineux.

**Impact:**

- Consommation excessive de mémoire
- Risque de DoS par payloads volumineux
- Dégradation des performances

**Recommandations:**

1. Configurer une limite de taille de payload dans FastAPI
2. Valider la taille des listes dans les payloads (ex: variants)
3. Implémenter une validation stricte avec Pydantic

**Exemple de correction:**

```python
from fastapi import Request
from fastapi.exceptions import RequestValidationError

# Dans main.py
app = FastAPI(
    title="Dynamac Backend API",
    description="Backend API for dynamic macOS wallpaper generation",
    version="1.0.0",
    # Limiter la taille des requêtes
    max_request_size=10 * 1024 * 1024,  # 10MB
)

# Dans routes/config.py
class TaskConfigPayload(BaseModel):
    variants: List[VariantConfig]

    @field_validator("variants")
    @classmethod
    def validate_variants_size(cls, value: List[VariantConfig]):
        if len(value) > 24:  # Maximum 24 heures dans une journée
            raise ValueError("Maximum 24 variants allowed")
        return value
```

---

### 2.4 Gestion des Secrets et Variables d'Environnement

**Sévérité:** Élevée  
**Fichier concerné:** `config.py`

**Description:**
Les secrets sont chargés depuis des variables d'environnement, mais il n'y a pas de validation de leur présence au démarrage. L'application peut démarrer avec des secrets manquants ou vides.

**Impact:**

- Erreurs runtime au lieu d'erreurs au démarrage
- Risque d'exposition de secrets si mal configuré
- Difficulté à détecter les problèmes de configuration

**Recommandations:**

1. Valider la présence et le format des secrets au démarrage
2. Utiliser des valeurs par défaut sécurisées (None) et vérifier explicitement
3. Ne jamais logger les secrets
4. Utiliser un gestionnaire de secrets (AWS Secrets Manager, HashiCorp Vault) en production

**Exemple de correction:**

```python
class Settings(BaseSettings):
    supabase_url: str
    supabase_service_key: str
    gemini_api_key: str

    @field_validator("supabase_url")
    @classmethod
    def validate_supabase_url(cls, v):
        if not v or not v.startswith("https://"):
            raise ValueError("SUPABASE_URL must be a valid HTTPS URL")
        return v

    @field_validator("supabase_service_key")
    @classmethod
    def validate_service_key(cls, v):
        if not v or len(v) < 20:
            raise ValueError("SUPABASE_SERVICE_KEY is required and must be valid")
        return v

    @field_validator("gemini_api_key")
    @classmethod
    def validate_gemini_key(cls, v):
        if not v or len(v) < 20:
            raise ValueError("GEMINI_API_KEY is required and must be valid")
        return v
```

---

## 3. Vulnérabilités Moyennes 🟡

### 3.1 Logs Potentiellement Sensibles

**Sévérité:** Moyenne  
**Fichiers concernés:** Tous les fichiers avec logging

**Description:**
Les logs peuvent contenir des informations sensibles (URLs, IDs de tâches, erreurs détaillées) qui pourraient être exploitées.

**Impact:**

- Exposition d'informations sur l'architecture
- Facilitation d'attaques ciblées
- Non-conformité RGPD si logs contiennent des données personnelles

**Recommandations:**

1. Ne jamais logger les secrets ou tokens
2. Sanitizer les URLs dans les logs (masquer les paramètres sensibles)
3. Utiliser des niveaux de log appropriés (ERROR au lieu de INFO pour les erreurs)
4. Implémenter un système de rotation des logs
5. Masquer les IDs de tâches dans les logs publics

**Exemple de correction:**

```python
import re

def sanitize_url(url: str) -> str:
    """Remove sensitive parameters from URLs in logs."""
    # Masquer les tokens dans les URLs
    url = re.sub(r'[?&]token=[^&]*', '?token=***', url)
    return url

logger.info(f"Upload successful: task_id={hash(task_id)}, file={file_name}")
logger.debug(f"Source URL: {sanitize_url(source_url)}")
```

---

### 3.2 Absence de Protection CSRF

**Sévérité:** Moyenne  
**Fichiers concernés:** Toutes les routes POST

**Description:**
Aucune protection CSRF n'est implémentée. Bien que CORS soit configuré, une protection CSRF supplémentaire est recommandée pour les opérations sensibles.

**Impact:**

- Risque d'attaques CSRF si l'origine autorisée est compromise
- Actions non autorisées effectuées au nom d'utilisateurs

**Recommandations:**

1. Implémenter des tokens CSRF pour les opérations sensibles
2. Utiliser le header `X-Requested-With` comme protection basique
3. Valider l'origine des requêtes POST

---

### 3.3 Gestion d'Erreurs Trop Verbale

**Sévérité:** Moyenne  
**Fichiers concernés:** Tous les fichiers de routes

**Description:**
Les messages d'erreur retournés aux clients peuvent révéler des informations sur l'architecture interne (noms de tables, structure de la base de données, chemins de fichiers).

**Impact:**

- Exposition d'informations sur l'architecture
- Facilitation d'attaques ciblées

**Recommandations:**

1. Généraliser les messages d'erreur pour les clients
2. Logger les détails complets côté serveur uniquement
3. Utiliser des codes d'erreur internes au lieu de messages détaillés

**Exemple de correction:**

```python
except Exception as e:
    logger.error(f"Upload failed: {str(e)}", exc_info=True)  # Détails dans les logs
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Failed to upload file"  # Message générique pour le client
    )
```

---

### 3.4 Absence de Timeout sur Certaines Opérations

**Sévérité:** Moyenne  
**Fichiers concernés:** `services/generation.py`, `services/upscale.py`

**Description:**
Bien qu'un timeout HTTP soit configuré (30s), certaines opérations de traitement d'images pourraient bloquer indéfiniment.

**Impact:**

- Consommation excessive de ressources
- Blocage de workers
- Dégradation des performances

**Recommandations:**

1. Ajouter des timeouts sur toutes les opérations I/O
2. Implémenter un timeout global pour les tâches en arrière-plan
3. Utiliser `asyncio.wait_for` pour les opérations asynchrones

---

### 3.5 Validation d'Extension de Fichier Basée sur le Nom

**Sévérité:** Moyenne  
**Fichier concerné:** `routes/upload.py`

**Description:**
L'extension du fichier est extraite du nom de fichier fourni par le client, qui peut être falsifié.

**Impact:**

- Risque de stockage de fichiers avec des extensions incorrectes
- Confusion sur le type réel de fichier

**Recommandations:**

1. Déterminer l'extension à partir du type MIME réel (après validation magic bytes)
2. Ignorer l'extension fournie par le client
3. Utiliser une extension sécurisée basée sur le type validé

**Exemple de correction:**

```python
# Après validation du type réel avec filetype
kind = filetype.guess(file_content)
extension_map = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp"
}
file_extension = extension_map.get(kind.mime, "jpg")
file_name = f"{uuid.uuid4()}.{file_extension}"
```

---

## 4. Bonnes Pratiques et Recommandations Générales 💡

### 4.1 Sécurité des Dépendances

**Recommandations:**

1. Utiliser `safety` ou `pip-audit` pour scanner les vulnérabilités connues
2. Épingler les versions exactes dans `requirements.txt` (actuellement certaines sont flexibles)
3. Mettre à jour régulièrement les dépendances
4. Utiliser `requirements.txt` avec versions exactes pour la production

**Action:**

```bash
pip install safety
safety check -r requirements.txt
```

---

### 4.2 Headers de Sécurité HTTP

**Recommandations:**
Ajouter des middlewares pour inclure des headers de sécurité :

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `X-XSS-Protection: 1; mode=block`
- `Strict-Transport-Security` (si HTTPS)
- `Content-Security-Policy`

**Exemple:**

```python
from fastapi.middleware.trustedhost import TrustedHostMiddleware

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response
```

---

### 4.3 Configuration de Production

**Recommandations:**

1. Désactiver le mode `reload=True` en production
2. Ne pas exposer sur `0.0.0.0` en production (utiliser un reverse proxy)
3. Configurer HTTPS obligatoire
4. Utiliser des variables d'environnement différentes pour dev/prod
5. Implémenter un système de monitoring et d'alertes

---

### 4.4 Validation des Entrées Utilisateur

**Recommandations:**

1. Valider tous les paramètres de route (task_id doit être un entier positif)
2. Valider les limites des listes (variants)
3. Valider les formats de chaînes (heures, URLs)
4. Utiliser Pydantic pour toutes les validations

**Exemple:**

```python
@router.get("/task/{task_id}/status")
async def get_task_status(task_id: int = Path(..., gt=0)):  # Doit être > 0
    # ... code existant
```

---

### 4.5 Gestion des Tâches en Arrière-Plan

**Recommandations:**

1. Implémenter un système de retry avec backoff exponentiel
2. Ajouter un système de queue (Celery, RQ) au lieu de BackgroundTasks pour la production
3. Implémenter un système de monitoring des tâches
4. Ajouter des timeouts stricts sur toutes les opérations

---

## 5. Checklist de Sécurité

### Priorité Critique (À faire immédiatement)

- [ ] Implémenter l'authentification/autorisation
- [ ] Valider les fichiers avec magic bytes
- [ ] Valider et restreindre les URLs sources
- [ ] Implémenter le rate limiting

### Priorité Élevée (À faire rapidement)

- [ ] Restreindre la configuration CORS
- [ ] Valider la présence des secrets au démarrage
- [ ] Limiter la taille des payloads JSON
- [ ] Ajouter des headers de sécurité HTTP

### Priorité Moyenne (À planifier)

- [ ] Sanitizer les logs
- [ ] Implémenter la protection CSRF
- [ ] Généraliser les messages d'erreur
- [ ] Améliorer la validation des extensions de fichiers

### Bonnes Pratiques

- [ ] Scanner les vulnérabilités des dépendances
- [ ] Épingler les versions exactes des dépendances
- [ ] Configurer un monitoring et des alertes
- [ ] Documenter les procédures de sécurité

---

## 6. Ressources et Outils Recommandés

### Outils de Sécurité

- **safety**: Scan de vulnérabilités Python (`pip install safety`)
- **bandit**: Analyse statique de sécurité Python (`pip install bandit`)
- **pip-audit**: Audit des dépendances (`pip install pip-audit`)
- **OWASP ZAP**: Test de pénétration automatisé
- **Snyk**: Scan continu de vulnérabilités

### Documentation

- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [FastAPI Security](https://fastapi.tiangolo.com/tutorial/security/)
- [Python Security Best Practices](https://python.readthedocs.io/en/stable/library/security.html)

---

## Conclusion

L'application présente plusieurs vulnérabilités critiques qui doivent être corrigées avant un déploiement en production. Les principales préoccupations sont l'absence d'authentification, la validation insuffisante des fichiers et des URLs, et l'absence de rate limiting.

Il est fortement recommandé de traiter toutes les vulnérabilités critiques avant de mettre l'application en production, et de mettre en place un processus de sécurité continue (audits réguliers, scans automatiques, revues de code).

---

**Note:** Cet audit est une analyse statique du code. Il est recommandé d'effectuer également des tests de pénétration dynamiques et une revue de sécurité par des experts avant le déploiement en production.
