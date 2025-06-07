# PubMed Search API

An enhanced API for searching PubMed articles, featuring modular logic, configuration management, and key-based authorization.

## 1. Setup and Configuration

### Step 1: Clone the Repository (if applicable)
```bash
git clone <repository-url>
cd <repository-directory>
```

### Step 2: Install Dependencies
Ensure you have Python 3.8+ installed. Then, install all required libraries:
```bash
pip install -r requirements.txt
```

### Step 3: Configure Environment Variables
Create a `.env` file in the project's root directory by copying the contents from `.env.example`.

```bash
# Rename .env.example to .env
cp .env.example .env
```

Open the `.env` file and set the values:
*   `API_KEY`: **Required**. Your secret key to access this API. You can generate a secure key, for example, with `openssl rand -hex 32`.
*   `NCBI_API_KEY`: **Optional, but recommended**. Your personal key from NCBI E-utils. Obtaining one will increase the request limit from 3 to 10 per second.
*   `REDIS_HOST`: The hostname for the Redis server. If using the provided `docker-compose.yml`, this should be `redis`.
*   `REDIS_PORT`: The port for the Redis server. Defaults to `6379`.

## 2. Running the Application

### Development
For local development, you can run the FastAPI server directly. You will need a separate Redis instance running.
```bash
python main.py
```
The server will be available at `http://127.0.0.1:8000`.

### Production (Recommended)
Use Docker Compose to build and run the application and the Redis cache together.

```bash
docker-compose up --build -d
```
*   `up`: Creates and starts the containers.
*   `--build`: Builds the API image before starting the services.
*   `-d`: Runs the containers in detached mode (in the background).

This single command will start both the API and Redis. The application will be available at `http://localhost:8000`.

## 3. Authorization

All API requests (except for `/` and `/docs`) must include an `X-API-Key` header with your secret key.

**Example with `curl`:**
```bash
curl -X 'GET' \
  'http://127.0.0.1:8000/search?query=covid' \
  -H 'accept: application/json' \
  -H 'X-API-Key: YOUR_SECRET_API_KEY'
```

## 4. How to Use the Search

This API provides powerful capabilities for searching PubMed.

### Basic Search
For a simple keyword search, use the `/search` or `/search/summary` endpoint with the `query` parameter.

### Advanced Search with Fields (Tags)
For a more precise search, use the `search_field` parameter, specifying a field tag.

**Common Search Fields:**

| Tag (`search_field`) | Search Field |
| :--- | :--- |
| `[AU]` | Author |
| `[JOUR]` | Journal |
| `[TI]` | Title |
| `[TIAB]` | Title/Abstract |
| `[MH]` | MeSH Terms |
| `[PT]` | Publication Type |

### Specialized Search: Guidelines with Full Text
The `/search/guidelines` endpoint searches for clinical guidelines with free full text available.

**Example:** Find guidelines for treating `hypertension`.
*   **URL:** `http://127.0.0.1:8000/search/guidelines?query=hypertension`

### Getting Formatted Citations
The `/search/citations` endpoint returns a list of structured citations in AMA style, each with a link.

**Example:** Get AMA citations for articles about `gene therapy`.
*   **URL:** `http://127.0.0.1:8000/search/citations?query=gene therapy`

**Example Response:**
```json
[
  {
    "citation": "Author A, Author B. Title of Article. *Journal Name*. Year;Vol(Issue):Pages.",
    "link": "https://pubmed.ncbi.nlm.nih.gov/12345678/"
  }
]
```

## 5. Deploying to a Server (e.g., AWS EC2)

### Step 1: Prepare Your Server
1.  **Launch an EC2 instance** (e.g., Ubuntu).
2.  **Configure Security Group** to allow inbound traffic on Port 22 (SSH) and Port 80 (HTTP).
3.  **Install Docker and Docker Compose**.
    *   For **Ubuntu**:
        ```bash
        # Install Docker
        sudo apt-get update
        sudo apt-get install -y docker.io
        sudo systemctl start docker && sudo systemctl enable docker
        sudo usermod -a -G docker $USER

        # Install Docker Compose
        sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
        sudo chmod +x /usr/local/bin/docker-compose
        ```
        (You will need to log out and log back in for the user group changes to apply).

### Step 2: Deploy the Application
1.  **Copy your project files** to the server (e.g., using `git clone`).
2.  **Create the `.env` file** on the server with your production secrets.
    ```bash
    nano .env
    ```
    Paste your `API_KEY`, `NCBI_API_KEY`, and set `REDIS_HOST=redis`.
3.  **Run Docker Compose**: From your project directory on the server, run:
    ```bash
    docker-compose up --build -d
    ```
Your API is now running on your AWS server. For a full production setup, consider running a web server like Nginx as a reverse proxy.
