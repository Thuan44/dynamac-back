# Dynamac Backend

Backend API Python pour une application de génération de fonds d'écran dynamiques macOS.

## 🚀 Stack Technique

- **FastAPI** - Framework web moderne et performant pour l'API REST
- **Supabase** - Base de données PostgreSQL et stockage d'objets via `supabase-py`
- **Python 3.11+** - Langage de programmation
- **Pydantic** - Validation de données et gestion des modèles
- **Uvicorn** - Serveur ASGI pour FastAPI

## 📁 Structure du Projet

```
project/
├── main.py                 # Point d'entrée FastAPI
├── config.py               # Variables d'environnement (Supabase URL, keys)
├── requirements.txt        # Dépendances Python
├── .env.example            # Template pour les variables d'environnement
├── .gitignore              # Fichiers ignorés par Git
├── models/
│   └── task.py            # Modèle Pydantic pour Task
├── services/
│   ├── storage.py         # Logique Supabase Storage
│   └── database.py        # Logique Postgres
└── routes/
    └── upload.py          # Route /upload
```

## 📋 Prérequis

- Python 3.11 ou supérieur
- Compte Supabase avec:
  - Un projet configuré
  - Un bucket Storage nommé `images`
  - Une table `tasks` dans PostgreSQL avec la structure suivante:

```sql
CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    url_source TEXT NOT NULL,
    state VARCHAR DEFAULT 'uploaded',
    expiration_date TIMESTAMP DEFAULT (NOW() + INTERVAL '24 hours'),
    created_at TIMESTAMP DEFAULT NOW()
);
```

## 🔧 Installation

1. **Cloner le repository**

```bash
git clone https://gitlab.com/dynamac/dynamac-back.git
cd dynamac-back
```

2. **Créer un environnement virtuel**

```bash
python3 -m venv venv
source venv/bin/activate  # Sur macOS/Linux
# ou
venv\Scripts\activate  # Sur Windows
```

3. **Installer les dépendances**

```bash
pip install -r requirements.txt
```

4. **Configurer les variables d'environnement**

Copiez le fichier `.env.example` vers `.env` et remplissez les valeurs:

```bash
cp .env.example .env
```

Éditez `.env` avec vos credentials Supabase:

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your_service_key_here
```

## 🚀 Démarrage

Lancer le serveur de développement:

```bash
uvicorn main:app --reload
```

Le serveur sera accessible sur `http://localhost:8000`

- **API Documentation**: `http://localhost:8000/docs` (Swagger UI)
- **Alternative Docs**: `http://localhost:8000/redoc` (ReDoc)

### Exposer l'API en HTTPS (Développement)

Pour exposer l'API en HTTPS et éviter les problèmes CORS en phase de développement, utilisez [localhost.run](https://localhost.run):

```bash
ssh -R 80:localhost:8000 localhost.run
```

Cette commande créera un tunnel HTTPS public vers votre serveur local. Vous recevrez une URL HTTPS (ex: `https://xxxxx.localhost.run`) que vous pourrez utiliser pour tester l'API depuis des applications web externes.

**Note**: Gardez le terminal ouvert pendant que vous utilisez le tunnel. Pour arrêter le tunnel, utilisez `Ctrl+C`.

## 📡 Endpoints

### POST `/api/upload`

Upload une image et crée une tâche associée.

**Request:**

- Method: `POST`
- Content-Type: `multipart/form-data`
- Body: fichier image (max 20MB)
- Types acceptés: `image/jpeg`, `image/jpg`, `image/png`, `image/webp`

**Response (201 Created):**

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "url_source": "https://your-project.supabase.co/storage/v1/object/public/images/uuid.jpg"
}
```

**Erreurs possibles:**

- `400 Bad Request`: Type de fichier invalide ou taille dépassée
- `500 Internal Server Error`: Erreur lors de l'upload ou de la création de la tâche

**Exemple avec curl:**

```bash
curl -X POST "http://localhost:8000/api/upload" \
  -H "accept: application/json" \
  -H "Content-Type: multipart/form-data" \
  -F "file=@/path/to/your/image.jpg"
```

### GET `/`

Endpoint racine retournant des informations sur l'API.

**Response:**

```json
{
  "message": "Dynamac Backend API",
  "version": "1.0.0"
}
```

### GET `/health`

Health check endpoint.

**Response:**

```json
{
  "status": "healthy"
}
```

## 🔒 Gestion des Erreurs

L'endpoint `/api/upload` implémente un mécanisme de rollback automatique:

1. Si l'upload vers Supabase Storage réussit mais la création de la tâche échoue, le fichier est automatiquement supprimé du Storage.
2. Si la création de la tâche réussit mais qu'une erreur survient ensuite, la tâche et le fichier sont supprimés.

## 🗄️ Configuration Supabase

### Créer le bucket Storage

1. Allez dans votre projet Supabase
2. Naviguez vers **Storage** > **Buckets**
3. Créez un nouveau bucket nommé `images`
4. Configurez les permissions appropriées (public read si nécessaire)

### Créer la table tasks

Exécutez cette requête SQL dans l'éditeur SQL de Supabase:

```sql
CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    url_source TEXT NOT NULL,
    state VARCHAR DEFAULT 'uploaded',
    expiration_date TIMESTAMP DEFAULT (NOW() + INTERVAL '24 hours'),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Optionnel: Créer un index sur expiration_date pour les requêtes de nettoyage
CREATE INDEX IF NOT EXISTS idx_tasks_expiration_date ON tasks(expiration_date);
```

## 🧪 Tests

Pour tester l'endpoint avec un fichier local:

```bash
curl -X POST "http://localhost:8000/api/upload" \
  -F "file=@test_image.jpg"
```

## 📝 Variables d'Environnement

| Variable               | Description                                | Requis |
| ---------------------- | ------------------------------------------ | ------ |
| `SUPABASE_URL`         | URL de votre projet Supabase               | Oui    |
| `SUPABASE_SERVICE_KEY` | Clé de service Supabase (service_role key) | Oui    |

## 🛠️ Développement

### Structure des Services

- **StorageService** (`services/storage.py`): Gère les opérations de stockage Supabase

  - `upload_file()`: Upload un fichier vers Supabase Storage
  - `delete_file()`: Supprime un fichier du Storage

- **DatabaseService** (`services/database.py`): Gère les opérations de base de données
  - `create_task()`: Crée une nouvelle tâche dans PostgreSQL
  - `delete_task()`: Supprime une tâche de la base de données

### Modèles Pydantic

- **TaskCreate**: Modèle pour créer une nouvelle tâche
- **TaskResponse**: Modèle de réponse pour une tâche
- **UploadResponse**: Modèle de réponse pour l'endpoint upload

## 📦 Dépendances

Voir `requirements.txt` pour la liste complète des dépendances.

Principales dépendances:

- `fastapi==0.104.1`
- `uvicorn[standard]==0.24.0`
- `python-multipart==0.0.6`
- `supabase==2.0.0`
- `pydantic==2.5.0`
- `pydantic-settings==2.1.0`

## 🤝 Contribution

Les contributions sont les bienvenues! N'hésitez pas à ouvrir une issue ou une merge request.

## 📄 License

[À définir]

## 👥 Auteurs

Dynamac Team
