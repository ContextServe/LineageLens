# LineageLens for ChatGPT Actions

Deploy LineageLens as a ChatGPT Action to give GPT-4 the ability to query Python code graphs.

## What is this?

This allows ChatGPT Actions to:
- Understand large Python codebases without reading full source
- Run impact analysis (what breaks if I change this?)
- Explore module structure and entry points
- Identify risk signals and resiliency issues
- Make informed changes with understanding of blast radius

## Prerequisites

- A public HTTPS domain (e.g., `lineagelens.your-company.com`)
- A hosting provider (Fly.io, Render, Railway, AWS, etc.)
- Your Python project to analyze
- OpenAI API access (to create the ChatGPT Action)

## Deployment Steps

### 1. Choose a Hosting Provider

**Option A: Fly.io (Recommended)**
```bash
# Install flyctl
brew install flyctl

# Login
flyctl auth login

# Create app
flyctl launch --image lineagelens/lineagelens:latest
# When prompted, say yes to save config

# Set environment variables
flyctl secrets set LINEAGELENS_API_KEY=your-secret-key-here
flyctl secrets set LINEAGELENS_LLM_API_KEY=sk-xxxxx  # Optional for LLM enrichment

# Deploy
flyctl deploy
```

**Option B: Render**
```
1. Push this repo to GitHub
2. Go to https://render.com/
3. New → Web Service
4. Connect GitHub repo
5. Build: `pip install -e '.[web,mcp,llm]'`
6. Start: `lineagelens serve .`
7. Set environment variables in Render dashboard
8. Deploy
```

**Option C: Railway**
```
1. Go to https://railway.app/
2. New Project → From GitHub
3. Select LineageLens repo
4. Add variables in Railway dashboard:
   - LINEAGELENS_API_KEY=your-secret
5. Deploy
```

### 2. Analyze Your Codebase

Once deployed, run the analysis:

```bash
# SSH into deployed container or run local then upload:
docker build -t lineagelens .
docker run -v $(pwd):/app lineagelens lineagelens analyze .

# Or mount the codebase volume in your deployment
```

The analysis creates `.lineagelens/graph.json` and `.lineagelens/report.json`.

### 3. Get Your OpenAPI Schema

Your deployment exposes an OpenAPI schema at:
```
https://your-domain.com/openapi.json
```

Or if using auth:
```
https://your-domain.com/docs
```

### 4. Create ChatGPT Action

1. Go to https://platform.openai.com/account/organization/overview
2. Click "Create New Action"
3. Name: `LineageLens Code Analysis`
4. Description: `Understand Python codebases by querying code graphs instead of reading source`
5. Paste your OpenAPI schema
6. Authentication:
   - Type: `API Key`
   - Header name: `X-API-Key` (if using auth)
   - Value: Your `LINEAGELENS_API_KEY`

### 5. Configure in ChatGPT

1. Go to https://chatgpt.com/
2. GPT-4 settings → Actions
3. Select `LineageLens Code Analysis`
4. Click "Authenticate" and paste your API key
5. Start asking!

## Example Queries

**Understand a module:**
```
"Give me an overview of the app.api module in my codebase"
```

**Check impact:**
```
"What would break if I removed the User.find method?"
```

**Find entry points:**
```
"List all API routes and CLI commands in the codebase"
```

**Risk analysis:**
```
"Show me all data write operations that might need protection"
```

## Environment Variables

```bash
# Required for API key auth
LINEAGELENS_API_KEY=your-secret-key

# Optional: for LLM enrichment (descriptions)
LINEAGELENS_LLM_API_KEY=sk-...

# Optional: override LLM model
LINEAGELENS_LLM_MODEL=gpt-4-turbo

# Optional: change server host/port (for local testing)
LINEAGELENS_HOST=0.0.0.0
LINEAGELENS_PORT=8000
```

## Troubleshooting

**"Failed to load graph"**
- Make sure you ran `lineagelens analyze .` in the container
- Check that `.lineagelens/graph.json` exists in the mounted volume

**"Unauthorized"**
- Check that your `X-API-Key` header value matches `LINEAGELENS_API_KEY`
- Re-authenticate in ChatGPT settings

**"Module not found"**
- Make sure the module name is fully qualified (e.g., `myapp.api`, not `api`)

## Cost

- **Hosting**: $5-50/month depending on provider and usage
- **API calls**: ~1 API token per query (very low cost)
- **LLM enrichment** (optional): ~1 cent per 1000 tokens if enabled

## Security Notes

1. **API Key**: Use a strong random key, rotate regularly
2. **Data**: Ensure your hosting provider meets compliance requirements
3. **HTTPS**: Always use HTTPS (required by ChatGPT)
4. **Firewall**: Restrict access by IP if on a private network
5. **Source code**: Graph only (no raw code) is exposed via API

## Advanced: Custom Deployment

To deploy on your own infrastructure:

```bash
# Build image
docker build -t my-lineagelens .

# Run container with volume mount
docker run -p 8000:8000 \
  -v /path/to/your/codebase:/app/project \
  -e LINEAGELENS_API_KEY=your-key \
  my-lineagelens \
  lineagelens serve /app/project
```

Ensure:
- Port 8000 is exposed and forwarded via HTTPS reverse proxy (nginx, Cloudflare, etc.)
- CORS is configured if accessing from multiple origins
- Rate limiting is in place to prevent abuse

## Support

Issues or questions? Check:
- Main README.md for CLI documentation
- docs/claude-mcp-setup.md for Claude Code integration
- GitHub Issues for bug reports
