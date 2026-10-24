"""The integrations AIVA offers and how each one connects.

Every integration has a workspace on/off switch (``IntegrationToggle``). How
it connects:

* ``oauth``: people click "Connect" and sign in at the provider. Personal
  integrations (a mailbox, a calendar, a Zoom or Drive account) are connected
  by each person; workspace ones (Slack, DocuSign, Facebook pages) once.
* ``credentials``: an admin pastes keys from the provider's console.
* ``webhook``: AIVA gives a URL that the other system posts to.
* ``api_key``: the other system calls AIVA's API (and receives webhooks).
* ``depends``: rides on another integration's connection (Google Meet uses
  the Google Calendar connection).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CredentialField:
    key: str
    label: str
    secret: bool = False
    required: bool = True
    help: str = ""
    placeholder: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "secret": self.secret, "required": self.required, "help": self.help, "placeholder": self.placeholder}


@dataclass(frozen=True)
class OAuthFamily:
    key: str
    label: str
    authorize_url: str
    token_url: str
    settings_client_id: str
    settings_client_secret: str
    pkce: bool = True
    scope_separator: str = " "
    basic_auth: bool = False  # send client credentials with HTTP Basic instead of the form body
    extra_params: dict[str, str] = field(default_factory=dict)
    console_url: str = ""


FAMILIES: dict[str, OAuthFamily] = {
    "google": OAuthFamily(
        "google", "Google", "https://accounts.google.com/o/oauth2/v2/auth", "https://oauth2.googleapis.com/token",
        "google_client_id", "google_client_secret",
        extra_params={"access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"},
        console_url="https://console.cloud.google.com/apis/credentials",
    ),
    "microsoft": OAuthFamily(
        "microsoft", "Microsoft", "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token", "microsoft_client_id", "microsoft_client_secret",
        extra_params={"prompt": "select_account"},
        console_url="https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade",
    ),
    "slack": OAuthFamily(
        "slack", "Slack", "https://slack.com/oauth/v2/authorize", "https://slack.com/api/oauth.v2.access",
        "slack_client_id", "slack_client_secret", pkce=False, scope_separator=",", console_url="https://api.slack.com/apps",
    ),
    "zoom": OAuthFamily(
        "zoom", "Zoom", "https://zoom.us/oauth/authorize", "https://zoom.us/oauth/token",
        "zoom_client_id", "zoom_client_secret", basic_auth=True, console_url="https://marketplace.zoom.us/develop/create",
    ),
    "dropbox": OAuthFamily(
        "dropbox", "Dropbox", "https://www.dropbox.com/oauth2/authorize", "https://api.dropboxapi.com/oauth2/token",
        "dropbox_app_key", "dropbox_app_secret", extra_params={"token_access_type": "offline"},
        console_url="https://www.dropbox.com/developers/apps",
    ),
    "docusign": OAuthFamily(
        "docusign", "DocuSign", "https://{docusign_host}/oauth/auth", "https://{docusign_host}/oauth/token",
        "docusign_integration_key", "docusign_secret_key", pkce=False, basic_auth=True,
        console_url="https://admindemo.docusign.com/apps-and-keys",
    ),
    "calendly": OAuthFamily(
        "calendly", "Calendly", "https://auth.calendly.com/oauth/authorize", "https://auth.calendly.com/oauth/token",
        "calendly_client_id", "calendly_client_secret", console_url="https://developer.calendly.com/console/apps",
    ),
    "facebook": OAuthFamily(
        "facebook", "Meta", "https://www.facebook.com/v21.0/dialog/oauth", "https://graph.facebook.com/v21.0/oauth/access_token",
        "facebook_app_id", "facebook_app_secret", pkce=False, scope_separator=",", console_url="https://developers.facebook.com/apps",
    ),
}


@dataclass(frozen=True)
class IntegrationDef:
    key: str
    name: str
    category: str
    vendor: str
    summary: str
    capabilities: tuple[str, ...]
    auth: str  # oauth | credentials | webhook | api_key | depends
    feature: str
    personal: bool = False
    family: str | None = None
    scopes: tuple[str, ...] = ()
    credential_fields: tuple[CredentialField, ...] = ()
    depends_on: str | None = None
    docs_url: str = ""
    color: str = "#4b4953"
    monogram: str = ""
    setup: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "category": self.category,
            "vendor": self.vendor,
            "summary": self.summary,
            "capabilities": list(self.capabilities),
            "auth": self.auth,
            "feature": self.feature,
            "personal": self.personal,
            "family": self.family,
            "scopes": list(self.scopes),
            "credential_fields": [f.to_dict() for f in self.credential_fields],
            "depends_on": self.depends_on,
            "docs_url": self.docs_url,
            "color": self.color,
            "monogram": self.monogram or self.name[:1],
            "setup": list(self.setup),
        }


GOOGLE_ID = ("openid", "email", "profile")
MS_ID = ("openid", "email", "profile", "offline_access", "User.Read")

INTEGRATIONS: dict[str, IntegrationDef] = {
    i.key: i
    for i in (
        # ------------------------------------------------------------ Email
        IntegrationDef(
            "gmail", "Gmail", "Email", "Google",
            "Send from your Gmail or Google Workspace address and bring replies from customers into AIVA.",
            (
                "Emails, campaigns and sequence steps go out from your own Gmail address",
                "Customer emails in your inbox are added to the matching contact's timeline",
                "Replies stop sequences automatically",
                "Each person connects their own mailbox; nothing is shared",
            ),
            "oauth", "mailbox_sync", personal=True, family="google",
            scopes=GOOGLE_ID + ("https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/gmail.readonly"),
            docs_url="https://developers.google.com/gmail/api", color="#ea4335", monogram="G",
            setup=("Turn Gmail on here (admins).", "Each person clicks Connect and signs in with Google.", "Pick whether AIVA should read the inbox for customer replies."),
        ),
        IntegrationDef(
            "outlook_mail", "Microsoft Outlook Mail", "Email", "Microsoft",
            "Send from your Outlook or Microsoft 365 address and bring customer replies into AIVA.",
            (
                "Emails, campaigns and sequence steps go out from your own Outlook address",
                "Customer emails in your inbox are added to the matching contact's timeline",
                "Replies stop sequences automatically",
                "Works with Microsoft 365 work accounts and Outlook.com",
            ),
            "oauth", "mailbox_sync", personal=True, family="microsoft",
            scopes=MS_ID + ("https://graph.microsoft.com/Mail.Send", "https://graph.microsoft.com/Mail.Read"),
            docs_url="https://learn.microsoft.com/graph/outlook-mail-concept-overview", color="#0a64ad", monogram="O",
            setup=("Turn Outlook Mail on here (admins).", "Each person clicks Connect and signs in with Microsoft.", "Your Microsoft 365 admin may need to approve the app once."),
        ),
        # ------------------------------------------------------------ Calendar
        IntegrationDef(
            "google_calendar", "Google Calendar", "Calendar", "Google",
            "Two-way sync between AIVA meetings and Google Calendar.",
            (
                "Meetings you schedule in AIVA appear in your Google Calendar, with invites",
                "Customer meetings booked in Google Calendar show up on the right contact, account and deal",
                "Changes and cancellations sync both ways",
                "Powers Google Meet links on AIVA meetings",
            ),
            "oauth", "calendar_sync", personal=True, family="google",
            scopes=GOOGLE_ID + ("https://www.googleapis.com/auth/calendar.events", "https://www.googleapis.com/auth/calendar.calendarlist.readonly"),
            docs_url="https://developers.google.com/calendar/api", color="#1a73e8", monogram="31",
            setup=("Turn Google Calendar on here (admins).", "Each person clicks Connect and signs in with Google.", "Choose which events to bring into AIVA."),
        ),
        IntegrationDef(
            "outlook_calendar", "Microsoft Outlook Calendar", "Calendar", "Microsoft",
            "Two-way sync between AIVA meetings and Outlook Calendar.",
            (
                "Meetings you schedule in AIVA appear in your Outlook Calendar",
                "Customer meetings booked in Outlook show up on the right contact, account and deal",
                "Changes and cancellations sync both ways",
            ),
            "oauth", "calendar_sync", personal=True, family="microsoft",
            scopes=MS_ID + ("https://graph.microsoft.com/Calendars.ReadWrite",),
            docs_url="https://learn.microsoft.com/graph/outlook-calendar-concept-overview", color="#0f6cbd", monogram="O",
            setup=("Turn Outlook Calendar on here (admins).", "Each person clicks Connect and signs in with Microsoft.", "Choose which events to bring into AIVA."),
        ),
        # ------------------------------------------------------------ Communication
        IntegrationDef(
            "microsoft_teams", "Microsoft Teams", "Communication", "Microsoft",
            "Post deal, lead and case alerts to a Teams channel and add Teams links to meetings.",
            (
                "Alerts for new leads, won deals, urgent cases and more in a Teams channel",
                "Workflows can post a message to Teams",
                "Add a Teams meeting link to AIVA meetings (each person connects their Microsoft account)",
            ),
            "credentials", "chat_integrations", family="microsoft",
            scopes=MS_ID + ("https://graph.microsoft.com/OnlineMeetings.ReadWrite",),
            credential_fields=(
                CredentialField("webhook_url", "Channel webhook URL", secret=True,
                                help="In Teams: the channel's ••• menu → Workflows → \"Post to a channel when a webhook request is received\", then copy the URL.",
                                placeholder="https://…logic.azure.com/…"),
            ),
            docs_url="https://learn.microsoft.com/microsoftteams/platform/webhooks-and-connectors/how-to/add-incoming-webhook",
            color="#5b5fc7", monogram="T",
            setup=("Create a channel webhook in Teams and paste it here.", "Pick which events to post.", "Optional: each person connects Microsoft to add Teams links to meetings."),
        ),
        IntegrationDef(
            "slack", "Slack", "Communication", "Slack",
            "Post alerts to Slack channels and share records with your team.",
            (
                "Alerts for new leads, won deals, urgent cases and more in the channel you choose",
                "Share any record to a Slack channel from AIVA",
                "Workflows can post a message to Slack",
            ),
            "oauth", "chat_integrations", family="slack",
            scopes=("chat:write", "chat:write.public", "channels:read", "groups:read", "team:read"),
            credential_fields=(
                CredentialField("webhook_url", "Incoming webhook URL (instead of signing in)", secret=True, required=False,
                                help="Starts with https://hooks.slack.com/services/.",
                                placeholder="https://hooks.slack.com/services/…"),
            ),
            docs_url="https://api.slack.com/messaging/sending", color="#4a154b", monogram="S",
            setup=("Click Connect and add AIVA to your Slack workspace.", "Choose the default channel and which events to post."),
        ),
        IntegrationDef(
            "whatsapp", "WhatsApp Business Platform", "Communication", "Meta",
            "Message customers on WhatsApp from AIVA and see their replies on the record.",
            (
                "Send WhatsApp messages and approved templates from any contact or lead",
                "Replies arrive in AIVA and are matched to the contact by phone number",
                "Delivery and read receipts",
            ),
            "credentials", "messaging",
            credential_fields=(
                CredentialField("phone_number_id", "Phone number ID", help="WhatsApp Manager → API setup → Phone number ID."),
                CredentialField("business_account_id", "WhatsApp Business Account ID", help="Used to list your approved message templates."),
                CredentialField("access_token", "Access token", secret=True, help="A permanent system-user token with whatsapp_business_messaging and whatsapp_business_management."),
                CredentialField("app_secret", "App secret", secret=True, help="Meta app → Settings → Basic. Used to check that webhooks really come from Meta."),
            ),
            docs_url="https://developers.facebook.com/docs/whatsapp/cloud-api", color="#25d366", monogram="W",
            setup=("Paste your phone number ID, business account ID, token and app secret.", "In the Meta app, set the webhook URL and verify token shown here and subscribe to messages.", "Send a test message."),
        ),
        IntegrationDef(
            "twilio_sms", "Twilio SMS", "Communication", "Twilio",
            "Text customers from AIVA and keep the conversation on the record.",
            (
                "Send SMS from any contact or lead, with your Twilio number",
                "Replies arrive in AIVA and are matched to the contact by phone number",
                "Delivery status for every message; STOP opt-outs are respected",
            ),
            "credentials", "messaging",
            credential_fields=(
                CredentialField("account_sid", "Account SID", placeholder="AC…"),
                CredentialField("auth_token", "Auth token", secret=True),
                CredentialField("from_number", "Twilio phone number", required=False, help="E.164 format, like +12145550100. Or use a messaging service below.", placeholder="+12145550100"),
                CredentialField("messaging_service_sid", "Messaging service SID", required=False, placeholder="MG…"),
            ),
            docs_url="https://www.twilio.com/docs/messaging", color="#f22f46", monogram="T",
            setup=("Paste your account SID, auth token and number.", "In Twilio, set the number's \"A message comes in\" webhook to the URL shown here.", "Send a test message."),
        ),
        # ------------------------------------------------------------ Meetings
        IntegrationDef(
            "zoom", "Zoom", "Meetings", "Zoom",
            "Create Zoom meetings for AIVA meetings automatically.",
            (
                "Pick Zoom when scheduling and AIVA creates the meeting and adds the join link",
                "Rescheduling or cancelling in AIVA updates Zoom",
                "Each person connects their own Zoom account",
            ),
            "oauth", "chat_integrations", personal=True, family="zoom",
            # Zoom apps carry their own scopes (classic or granular), so none are requested here.
            scopes=(),
            docs_url="https://developers.zoom.us/docs/api/", color="#0b5cff", monogram="Z",
            setup=("Turn Zoom on here (admins).", "Each person clicks Connect and signs in with Zoom.", "Choose Zoom as the meeting type when scheduling."),
        ),
        IntegrationDef(
            "google_meet", "Google Meet", "Meetings", "Google",
            "Add Google Meet links to AIVA meetings.",
            (
                "Pick Google Meet when scheduling and AIVA adds a Meet link",
                "Uses each person's Google Calendar connection, so the meeting shows in their calendar too",
            ),
            "depends", "chat_integrations", depends_on="google_calendar", family="google",
            docs_url="https://developers.google.com/calendar/api/guides/create-events#conferencing", color="#00897b", monogram="M",
            setup=("Turn Google Calendar on and connect it.", "Turn Google Meet on here.", "Choose Google Meet as the meeting type when scheduling."),
        ),
        # ------------------------------------------------------------ Scheduling
        IntegrationDef(
            "calendly", "Calendly", "Scheduling", "Calendly",
            "Calendly bookings become AIVA meetings, linked to the right lead or contact.",
            (
                "New bookings create a meeting in AIVA and a lead if the person is new",
                "Cancellations and reschedules update the meeting",
                "Share your Calendly links from any record",
            ),
            "oauth", "calendar_sync", family="calendly",
            credential_fields=(
                CredentialField("personal_access_token", "Personal access token (instead of signing in)", secret=True, required=False,
                                help="Calendly → Integrations → API & Webhooks → Personal access tokens."),
            ),
            docs_url="https://developer.calendly.com/", color="#006bff", monogram="C",
            setup=("Click Connect and sign in with Calendly (or paste a personal access token).", "AIVA subscribes to new and cancelled bookings for you.", "Choose who owns new leads from bookings."),
        ),
        # ------------------------------------------------------------ Documents
        IntegrationDef(
            "google_drive", "Google Drive", "Documents", "Google",
            "Attach Google Drive files to accounts, contacts and deals.",
            (
                "Search your Drive from any record and attach files",
                "Copy a Drive file into AIVA Documents",
                "Each person connects their own Drive; links respect Drive sharing",
            ),
            "oauth", "documents", personal=True, family="google",
            scopes=GOOGLE_ID + ("https://www.googleapis.com/auth/drive.readonly",),
            docs_url="https://developers.google.com/drive/api", color="#1fa463", monogram="D",
            setup=("Turn Google Drive on here (admins).", "Each person clicks Connect.", "Use \"Cloud files\" on any record to attach files."),
        ),
        IntegrationDef(
            "onedrive", "Microsoft OneDrive", "Documents", "Microsoft",
            "Attach OneDrive and SharePoint files to accounts, contacts and deals.",
            (
                "Search your OneDrive from any record and attach files",
                "Copy a file into AIVA Documents",
                "Each person connects their own account",
            ),
            "oauth", "documents", personal=True, family="microsoft",
            scopes=MS_ID + ("https://graph.microsoft.com/Files.Read.All",),
            docs_url="https://learn.microsoft.com/graph/onedrive-concept-overview", color="#0364b8", monogram="O",
            setup=("Turn OneDrive on here (admins).", "Each person clicks Connect.", "Use \"Cloud files\" on any record to attach files."),
        ),
        IntegrationDef(
            "dropbox", "Dropbox", "Documents", "Dropbox",
            "Attach Dropbox files to accounts, contacts and deals.",
            (
                "Search your Dropbox from any record and attach files",
                "Copy a file into AIVA Documents",
                "Each person connects their own account",
            ),
            "oauth", "documents", personal=True, family="dropbox",
            scopes=("account_info.read", "files.metadata.read", "files.content.read", "sharing.read"),
            docs_url="https://www.dropbox.com/developers/documentation", color="#0061fe", monogram="D",
            setup=("Turn Dropbox on here (admins).", "Each person clicks Connect.", "Use \"Cloud files\" on any record to attach files."),
        ),
        # ------------------------------------------------------------ E-signature
        IntegrationDef(
            "docusign", "DocuSign", "E-signature", "DocuSign",
            "Send quotes and documents for signature and track them on the deal.",
            (
                "Send a quote or any AIVA document for signature from the record",
                "See who has viewed and signed, and get notified when it's complete",
                "The signed copy is saved back to AIVA Documents",
            ),
            "oauth", "e_signature", family="docusign", scopes=("signature", "extended"),
            docs_url="https://developers.docusign.com/docs/esign-rest-api/", color="#ffc820", monogram="D",
            setup=("Click Connect and sign in with your DocuSign account.", "Send from a deal, quote, contact or document."),
        ),
        # ------------------------------------------------------------ Lead capture
        IntegrationDef(
            "wordpress_forms", "WordPress Forms", "Lead Capture", "WordPress",
            "Turn submissions from your WordPress site's forms into AIVA leads.",
            (
                "Works with Contact Form 7, WPForms, Gravity Forms, Elementor, Ninja Forms and Formidable",
                "Install the small AIVA plugin, or point any form's webhook at AIVA",
                "Map form fields to lead fields; duplicates are updated instead of created",
            ),
            "webhook", "web_forms",
            docs_url="https://wordpress.org/plugins/", color="#21759b", monogram="W",
            setup=("Turn WordPress Forms on.", "Download the AIVA plugin (it already has your workspace's address and key) and activate it on your site.", "Submit a test entry."),
        ),
        IntegrationDef(
            "facebook_lead_ads", "Facebook Lead Ads", "Lead Capture", "Meta",
            "New leads from Facebook and Instagram lead ads arrive in AIVA within seconds.",
            (
                "Every lead-form submission on your pages becomes an AIVA lead",
                "Map form questions to lead fields",
                "Campaign, ad and form names are kept on the lead for reporting",
            ),
            "oauth", "web_forms", family="facebook",
            scopes=("pages_show_list", "pages_read_engagement", "pages_manage_metadata", "leads_retrieval", "business_management"),
            docs_url="https://developers.facebook.com/docs/marketing-api/guides/lead-ads", color="#0866ff", monogram="f",
            setup=("Click Connect and sign in with Facebook.", "Choose the pages whose lead forms should send leads to AIVA.", "Use Meta's lead ads testing tool to send a test lead."),
        ),
        # ------------------------------------------------------------ Automation
        IntegrationDef(
            "zapier", "Zapier", "Automation", "Zapier",
            "Connect AIVA to thousands of apps with Zaps.",
            (
                "Triggers: new or updated leads, contacts, accounts and deals, deals won or lost, new cases and more",
                "Actions: create and update records through the AIVA API",
                "Use \"Webhooks by Zapier\" with the URLs and API keys from this page",
            ),
            "api_key", "api_access",
            docs_url="https://zapier.com/apps/webhook/integrations", color="#ff4f00", monogram="Z",
            setup=("Create an API key for Zapier.", "Add a trigger: paste a \"Catch Hook\" URL here and pick the event.", "For actions, call the AIVA API with the key."),
        ),
        IntegrationDef(
            "make", "Make", "Automation", "Make",
            "Build Make scenarios that react to AIVA events and update AIVA.",
            (
                "Instant triggers with Make custom webhooks",
                "Update AIVA from any scenario with the HTTP module and an API key",
                "Signed payloads so scenarios can check they came from AIVA",
            ),
            "api_key", "api_access",
            docs_url="https://www.make.com/en/help/tools/webhooks", color="#6d00cc", monogram="M",
            setup=("Create an API key for Make.", "Add a Custom webhook in Make, paste its URL here and pick events.", "Use the HTTP module with the key to change AIVA."),
        ),
        IntegrationDef(
            "n8n", "n8n", "Automation", "n8n",
            "Self-hosted or cloud n8n workflows triggered by AIVA events.",
            (
                "Instant triggers with the n8n Webhook node",
                "Update AIVA with the HTTP Request node and an API key",
                "Signed payloads (X-AIVA-Signature) for verification",
            ),
            "api_key", "api_access",
            docs_url="https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/", color="#ea4b71", monogram="n8n",
            setup=("Create an API key for n8n.", "Add a Webhook node, paste its production URL here and pick events.", "Use HTTP Request nodes with the key to change AIVA."),
        ),
    )
}

CATEGORIES = ("Email", "Calendar", "Communication", "Meetings", "Scheduling", "Documents", "E-signature", "Lead Capture", "Automation")


def get(key: str) -> IntegrationDef | None:
    return INTEGRATIONS.get(key)
