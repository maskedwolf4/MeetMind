# Teams Bot Setup Guide

Step-by-step instructions for configuring the MeetMind Teams bot. A person with Azure AD admin access should follow these instructions to enable the Teams live-join functionality.

## Prerequisites

- An Azure subscription (free tier works for dev/test)
- Azure AD tenant admin access (or the ability to request admin consent)
- A publicly accessible HTTPS endpoint for the bot (use ngrok for local development)

---

## Step 1: Create an Azure AD App Registration

1. Go to [Azure Portal → Azure Active Directory → App registrations](https://portal.azure.com/#view/Microsoft_AAD_IAM/ActiveDirectoryMenuBlade/~/RegisteredApps)
2. Click **"New registration"**
3. Fill in:
   - **Name:** `MeetMind Bot`
   - **Supported account types:** "Accounts in this organizational directory only" (single tenant) — or "Accounts in any organizational directory" if you need multi-tenant
   - **Redirect URI:** Leave blank for now (we'll use client credentials, not user-delegated auth)
4. Click **Register**
5. On the app's overview page, copy these values:
   - **Application (client) ID** → this is your `AZURE_APP_ID`
   - **Directory (tenant) ID** → this is your `AZURE_TENANT_ID`

## Step 2: Create a Client Secret

1. In the app registration, go to **Certificates & secrets** → **Client secrets**
2. Click **"New client secret"**
3. Set a description (e.g., "MeetMind Bot Secret") and expiration (recommended: 24 months)
4. Click **Add**
5. **IMMEDIATELY copy the secret value** (it will only be shown once) → this is your `AZURE_APP_PASSWORD`

## Step 3: Configure API Permissions

The bot needs these **Application permissions** (not delegated — the bot acts as itself, not on behalf of a user):

1. In the app registration, go to **API permissions** → **Add a permission**
2. Select **Microsoft Graph** → **Application permissions**
3. Add the following permissions:

| Permission | Purpose |
|------------|---------|
| `Calls.Initiate.All` | Join/initiate calls |
| `Calls.InitiateGroupCall.All` | Join group calls (meetings) |
| `Calls.JoinGroupCall.All` | Join group calls as guest |
| `Calls.AccessMedia.All` | Access call media (for transcript) |
| `OnlineMeetings.Read.All` | Read meeting details and transcripts |
| `CallRecords.Read.All` | Read call records after calls end |

4. Click **"Grant admin consent for [Your Tenant]"** — this requires tenant admin privileges
5. Verify all permissions show a green checkmark under "Status"

> **⚠️ Important:** Without admin consent, the bot will fail to acquire tokens with the required scopes. If you're not a tenant admin, forward the app registration ID to your IT admin and ask them to grant consent in the Azure Portal.

## Step 4: Create a Bot Channel Registration

1. Go to [Azure Portal → Create a resource](https://portal.azure.com/#create/hub) → search for **"Azure Bot"**
2. Click **Create**
3. Fill in:
   - **Bot handle:** `meetmind-bot`
   - **Subscription:** Your Azure subscription
   - **Resource group:** Create new or use existing (e.g., `meetmind-rg`)
   - **Pricing tier:** F0 (Free) for development
   - **Microsoft App ID:** Select "Use existing app registration" and enter your `AZURE_APP_ID`
   - **Microsoft App ID type:** "Single Tenant" or "Multi Tenant" (match Step 1)
4. Click **Create**

## Step 5: Configure the Messaging Endpoint

1. In the Azure Bot resource, go to **Configuration**
2. Set the **Messaging endpoint** to your bot's public URL:
   - **Local development:** `https://<your-ngrok-subdomain>.ngrok-free.app/api/messages`
   - **Production:** `https://your-domain.com/api/messages`
3. Click **Apply**

## Step 6: Enable the Teams Channel

1. In the Azure Bot resource, go to **Channels**
2. Click **Microsoft Teams**
3. Under the **Calling** tab:
   - Check **"Enable calling"**
   - Set the **Webhook URL** to: `https://<your-domain>/api/calls`
4. Click **Save**
5. Accept the Terms of Service

## Step 7: Configure Your Environment Variables

Add these to your `backend/.env` file:

```env
AZURE_APP_ID=<Application (client) ID from Step 1>
AZURE_APP_PASSWORD=<Client secret value from Step 2>
AZURE_TENANT_ID=<Directory (tenant) ID from Step 1>
GRAPH_API_SCOPE=https://graph.microsoft.com/.default
BOT_FRAMEWORK_ENDPOINT=https://<your-public-url>
```

## Step 8: Verify the Setup

Start the MeetMind backend:

```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

Check the startup logs. You should see:

```
✅ Azure prerequisites validated — Graph API token acquired successfully
Teams live-join: ENABLED
```

If you see:

```
⚠️  TEAMS BOT DEGRADED MODE: Azure credentials not configured.
    Missing env vars: AZURE_APP_ID, AZURE_APP_PASSWORD, AZURE_TENANT_ID.
    Teams live-join endpoints will return 503.
```

Then re-check your `.env` file and ensure all three values are set correctly.

## Step 9: Install the Bot in Teams (for Testing)

1. Open the `backend/bots/teams_bot/manifest.json` file
2. Replace `{{AZURE_APP_ID}}` with your actual Application ID
3. Create a ZIP file containing `manifest.json` and the icon files
4. In Microsoft Teams:
   - Go to **Apps** → **Manage your apps** → **Upload a custom app**
   - Select the ZIP file
   - Click **Add**

## Troubleshooting

### "Failed to acquire Graph token"
- Verify `AZURE_APP_ID`, `AZURE_APP_PASSWORD`, and `AZURE_TENANT_ID` are correct
- Ensure admin consent has been granted for all API permissions
- Check that the client secret hasn't expired

### "Graph /communications/calls failed"
- Verify `Calls.JoinGroupCall.All` permission is granted with admin consent
- Ensure the meeting URL is a valid Teams join link
- Check that the bot's messaging endpoint is accessible from the internet

### "Bot not responding in Teams"
- Verify the messaging endpoint URL is correct and publicly accessible
- Check ngrok tunnel is running (for local development)
- Review Azure Bot Service health in the Azure Portal

### Permissions Not Showing Green Checkmarks
- You need **tenant admin** access to grant admin consent
- Contact your IT admin to navigate to:
  `https://login.microsoftonline.com/{tenant-id}/adminconsent?client_id={app-id}`

---

## Local Development with ngrok

For local development, use ngrok to expose your local server:

```bash
# Install ngrok
# https://ngrok.com/download

# Start your backend
uvicorn app.main:app --reload --port 8000

# In another terminal, start ngrok
ngrok http 8000

# Copy the HTTPS URL (e.g., https://abc123.ngrok-free.app)
# Update Azure Bot messaging endpoint to: https://abc123.ngrok-free.app/api/messages
# Update BOT_FRAMEWORK_ENDPOINT in .env to: https://abc123.ngrok-free.app
```

> **Note:** ngrok URLs change each time you restart (unless you have a paid plan with a fixed subdomain). You'll need to update the Azure Bot messaging endpoint each time.
