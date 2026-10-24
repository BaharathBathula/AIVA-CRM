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
    "constant_contact": OAuthFamily(
        "constant_contact", "Constant Contact", "https://authz.constantcontact.com/oauth2/default/v1/authorize",
        "https://authz.constantcontact.com/oauth2/default/v1/token", "constant_contact_client_id", "constant_contact_client_secret",
        basic_auth=True, console_url="https://app.constantcontact.com/pages/dma/portal/",
    ),
    "intuit": OAuthFamily(
        "intuit", "Intuit", "https://appcenter.intuit.com/connect/oauth2", "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
        "quickbooks_client_id", "quickbooks_client_secret", pkce=False, basic_auth=True,
        console_url="https://developer.intuit.com/app/developer/myapps",
    ),
    "xero": OAuthFamily(
        "xero", "Xero", "https://login.xero.com/identity/connect/authorize", "https://identity.xero.com/connect/token",
        "xero_client_id", "xero_client_secret", basic_auth=True, console_url="https://developer.xero.com/app/manage",
    ),
    "freshbooks": OAuthFamily(
        "freshbooks", "FreshBooks", "https://auth.freshbooks.com/oauth/authorize", "https://api.freshbooks.com/auth/oauth/token",
        "freshbooks_client_id", "freshbooks_client_secret", pkce=False, console_url="https://my.freshbooks.com/#/developer",
    ),
    "sage": OAuthFamily(
        "sage", "Sage", "https://www.sageone.com/oauth2/auth/central?filter=apiv3.1", "https://oauth.accounting.sage.com/token",
        "sage_client_id", "sage_client_secret", pkce=False, console_url="https://developerselfservice.sageone.com/",
    ),
    "linkedin": OAuthFamily(
        "linkedin", "LinkedIn", "https://www.linkedin.com/oauth/v2/authorization", "https://www.linkedin.com/oauth/v2/accessToken",
        "linkedin_client_id", "linkedin_client_secret", pkce=False, console_url="https://www.linkedin.com/developers/apps",
    ),
}


# Sign-in and provisioning: changed only by an admin signed in to the AIVA app,
# never with an API key and never by AIVA support (same rule as /identity).
APP_ONLY_KEYS = frozenset({"entra_id", "okta"})


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
        # ------------------------------------------------------------ Email marketing
        IntegrationDef(
            "mailchimp", "Mailchimp", "Email Marketing", "Intuit Mailchimp",
            "Keep Mailchimp audiences in step with your AIVA contacts and leads.",
            (
                "Add contacts and leads to an audience automatically, with tags and merge fields",
                "Unsubscribes and cleaned addresses in Mailchimp update AIVA's do-not-contact list",
                "Campaign opens and clicks show on the person's timeline",
            ),
            "credentials", "mass_email",
            credential_fields=(
                CredentialField("api_key", "API key", secret=True, help="Mailchimp → Profile → Extras → API keys. It ends with your data center, like -us21.", placeholder="xxxxxxxx-us21"),
            ),
            docs_url="https://mailchimp.com/developer/marketing/", color="#ffe01b", monogram="MC",
            setup=("Paste an API key.", "Choose the audience and which contacts and leads to sync.", "AIVA registers a webhook so unsubscribes come back."),
        ),
        IntegrationDef(
            "activecampaign", "ActiveCampaign", "Email Marketing", "ActiveCampaign",
            "Sync contacts to ActiveCampaign lists and bring engagement back to AIVA.",
            (
                "Contacts and leads subscribed to the lists you choose, with tags and custom fields",
                "Unsubscribes and bounces update AIVA's do-not-contact list",
                "Opens, clicks and automations show on the timeline",
            ),
            "credentials", "mass_email",
            credential_fields=(
                CredentialField("api_url", "API URL", help="ActiveCampaign → Settings → Developer. Looks like https://youraccount.api-us1.com.", placeholder="https://youraccount.api-us1.com"),
                CredentialField("api_key", "API key", secret=True),
            ),
            docs_url="https://developers.activecampaign.com/reference/overview", color="#356ae6", monogram="AC",
            setup=("Paste the API URL and key.", "Pick the lists and which records to sync.", "AIVA adds a webhook for unsubscribes and bounces."),
        ),
        IntegrationDef(
            "brevo", "Brevo", "Email Marketing", "Brevo",
            "Sync AIVA contacts to Brevo lists for email and SMS campaigns.",
            (
                "Contacts and leads added to Brevo lists with their attributes",
                "Unsubscribes, hard bounces and spam complaints update AIVA's do-not-contact list",
                "Campaign engagement shows on the timeline",
            ),
            "credentials", "mass_email",
            credential_fields=(
                CredentialField("api_key", "API key", secret=True, help="Brevo → SMTP & API → API keys.", placeholder="xkeysib-…"),
            ),
            docs_url="https://developers.brevo.com/", color="#0b996e", monogram="B",
            setup=("Paste an API key.", "Choose the lists and which records to sync.", "AIVA registers a webhook for unsubscribes."),
        ),
        IntegrationDef(
            "constant_contact", "Constant Contact", "Email Marketing", "Constant Contact",
            "Sync AIVA contacts to Constant Contact lists.",
            (
                "Contacts and leads added to the lists you choose, with permission to send",
                "Unsubscribes in Constant Contact update AIVA's do-not-contact list",
                "Campaign activity shows on the timeline",
            ),
            "oauth", "mass_email", family="constant_contact", scopes=("account_read", "contact_data", "campaign_data", "offline_access"),
            docs_url="https://developer.constantcontact.com/api_guide/index.html", color="#1856ed", monogram="CC",
            setup=("Click Connect and sign in with Constant Contact.", "Choose the lists and which records to sync."),
        ),
        IntegrationDef(
            "klaviyo", "Klaviyo", "Email Marketing", "Klaviyo",
            "Send AIVA contacts and CRM events to Klaviyo profiles and lists.",
            (
                "Contacts and leads become Klaviyo profiles in the lists you choose",
                "CRM events (deal won, quote accepted…) can trigger Klaviyo flows",
                "Unsubscribes and suppressions update AIVA's do-not-contact list",
            ),
            "credentials", "mass_email",
            credential_fields=(
                CredentialField("api_key", "Private API key", secret=True, help="Klaviyo → Settings → API keys → Create private key (full access to profiles, lists, events and metrics).", placeholder="pk_…"),
            ),
            docs_url="https://developers.klaviyo.com/en/reference/api_overview", color="#232426", monogram="K",
            setup=("Paste a private API key.", "Choose the lists and which records to sync.", "Pick the CRM events to send as Klaviyo metrics."),
        ),
        # ------------------------------------------------------------ Advertising
        IntegrationDef(
            "google_ads", "Google Ads", "Advertising", "Google",
            "Capture Google Ads lead-form leads and send won deals back as conversions.",
            (
                "Lead-form extension submissions become AIVA leads",
                "Leads keep their click ID (GCLID), campaign and keyword",
                "Qualified and won deals are uploaded as offline conversions with their value",
                "Campaign spend and clicks next to the revenue they produced",
            ),
            "oauth", "google_ads", family="google", scopes=GOOGLE_ID + ("https://www.googleapis.com/auth/adwords",),
            docs_url="https://developers.google.com/google-ads/api/docs/start", color="#4285f4", monogram="Ad",
            setup=("Click Connect and sign in with the Google account that manages your ads.", "Pick the Google Ads account and your conversion actions.", "Add the lead-form webhook URL to your lead forms."),
        ),
        # ------------------------------------------------------------ Accounting
        IntegrationDef(
            "quickbooks", "QuickBooks Online", "Accounting", "Intuit",
            "Send invoices to QuickBooks and see when they're paid.",
            (
                "Accounts become QuickBooks customers (matched by name or email first)",
                "AIVA invoices are created in QuickBooks with their lines, tax and terms",
                "Payments recorded in QuickBooks mark the AIVA invoice paid",
                "Open balance and overdue invoices on the account",
            ),
            "oauth", "accounting_sync", family="intuit", scopes=("com.intuit.quickbooks.accounting",),
            docs_url="https://developer.intuit.com/app/developer/qbo/docs/get-started", color="#2ca01c", monogram="QB",
            setup=("Click Connect and choose your QuickBooks company.", "Map products to QuickBooks items and pick the income account and tax code.", "Choose whether invoices sync automatically or by hand."),
        ),
        IntegrationDef(
            "xero", "Xero", "Accounting", "Xero",
            "Send invoices to Xero and track payments in AIVA.",
            (
                "Accounts become Xero contacts",
                "AIVA invoices are created in Xero as draft or approved invoices",
                "Payments in Xero mark the AIVA invoice paid",
                "Outstanding and overdue amounts on the account",
            ),
            "oauth", "accounting_sync", family="xero",
            scopes=("openid", "profile", "email", "offline_access", "accounting.transactions", "accounting.contacts", "accounting.settings.read"),
            docs_url="https://developer.xero.com/documentation/", color="#13b5ea", monogram="X",
            setup=("Click Connect and choose your Xero organisation.", "Pick the sales account and tax rate for invoice lines.", "Choose whether invoices sync automatically or by hand."),
        ),
        IntegrationDef(
            "freshbooks", "FreshBooks", "Accounting", "FreshBooks",
            "Create FreshBooks clients and invoices from AIVA.",
            (
                "Accounts become FreshBooks clients",
                "AIVA invoices are created in FreshBooks",
                "Payments in FreshBooks mark the AIVA invoice paid",
            ),
            "oauth", "accounting_sync", family="freshbooks",
            scopes=("user:profile:read", "user:clients:read", "user:clients:write", "user:invoices:read", "user:invoices:write", "user:payments:read"),
            docs_url="https://www.freshbooks.com/api/start", color="#0075dd", monogram="FB",
            setup=("Click Connect and choose your FreshBooks business.", "Choose whether invoices sync automatically or by hand."),
        ),
        IntegrationDef(
            "sage_accounting", "Sage Accounting", "Accounting", "Sage",
            "Send invoices to Sage Accounting and follow their payment.",
            (
                "Accounts become Sage customers",
                "AIVA invoices are created as Sage sales invoices",
                "Payments in Sage mark the AIVA invoice paid",
            ),
            "oauth", "accounting_sync", family="sage", scopes=("full_access",),
            docs_url="https://developer.sage.com/accounting/", color="#00d639", monogram="Sg",
            setup=("Click Connect and choose your Sage business.", "Pick the sales ledger account and tax rate.", "Choose whether invoices sync automatically or by hand."),
        ),
        # ------------------------------------------------------------ Payments
        IntegrationDef(
            "stripe", "Stripe", "Payments", "Stripe",
            "Get paid by card: payment links on invoices, paid status straight back in AIVA.",
            (
                "A Stripe payment link on every AIVA invoice, emailed or shared from the record",
                "Payments, refunds and failed payments update the invoice and its account",
                "Stripe customers matched to AIVA accounts, with their payments on the timeline",
            ),
            "credentials", "online_payments",
            credential_fields=(
                CredentialField("secret_key", "Secret or restricted key", secret=True, help="Stripe Dashboard → Developers → API keys. A restricted key needs write access to Checkout, Payment Links, Products, Prices and Customers, and read access to everything else.", placeholder="rk_live_… or sk_live_…"),
            ),
            docs_url="https://docs.stripe.com/api", color="#635bff", monogram="S",
            setup=("Paste a secret or restricted key.", "AIVA creates the webhook endpoint in your Stripe account for you.", "Add a payment link to an invoice."),
        ),
        IntegrationDef(
            "paypal", "PayPal", "Payments", "PayPal",
            "Collect invoice payments with PayPal and see them in AIVA.",
            (
                "PayPal checkout links on AIVA invoices",
                "Completed and refunded payments update the invoice",
                "Webhooks verified with PayPal before anything changes",
            ),
            "credentials", "online_payments",
            credential_fields=(
                CredentialField("client_id", "Client ID", help="developer.paypal.com → Apps & Credentials → your REST app."),
                CredentialField("client_secret", "Secret", secret=True),
                CredentialField("environment", "Environment", help="live or sandbox", placeholder="live"),
            ),
            docs_url="https://developer.paypal.com/api/rest/", color="#003087", monogram="PP",
            setup=("Paste your REST app's client ID and secret.", "AIVA registers the webhook in your PayPal app.", "Add a PayPal link to an invoice."),
        ),
        IntegrationDef(
            "square", "Square", "Payments", "Square",
            "Square payment links on invoices, with payments synced back.",
            (
                "Square checkout links on AIVA invoices",
                "Completed payments and refunds update the invoice",
                "Square customers matched to AIVA contacts",
            ),
            "credentials", "online_payments",
            credential_fields=(
                CredentialField("access_token", "Access token", secret=True, help="Square Developer Console → your app → Credentials (production or sandbox)."),
                CredentialField("environment", "Environment", help="production or sandbox", placeholder="production"),
                CredentialField("location_id", "Location ID", required=False, help="Leave blank to use your main location."),
                CredentialField("webhook_signature_key", "Webhook signature key", secret=True, required=False, help="Shown when you add AIVA's webhook URL in the Square Developer Console."),
            ),
            docs_url="https://developer.squareup.com/docs", color="#1a1a1a", monogram="Sq",
            setup=("Paste an access token.", "Add AIVA's webhook URL in Square and paste the signature key.", "Add a Square link to an invoice."),
        ),
        IntegrationDef(
            "chargebee", "Chargebee", "Payments", "Chargebee",
            "See subscriptions, MRR and renewals on AIVA accounts.",
            (
                "Chargebee customers matched to AIVA accounts",
                "Subscriptions, plan, MRR and renewal dates on the account",
                "Cancellations and failed payments raise tasks for the account owner",
            ),
            "credentials", "online_payments",
            credential_fields=(
                CredentialField("site", "Site", help="The part before .chargebee.com in your Chargebee address.", placeholder="acme"),
                CredentialField("api_key", "API key", secret=True, help="Chargebee → Settings → Configure Chargebee → API keys (full access or read-only)."),
            ),
            docs_url="https://apidocs.chargebee.com/docs/api", color="#ff3300", monogram="CB",
            setup=("Paste your site and an API key.", "Add AIVA's webhook URL in Chargebee.", "Choose what happens on cancellations and failed payments."),
        ),
        # ------------------------------------------------------------ Customer support
        IntegrationDef(
            "zendesk", "Zendesk", "Customer Support", "Zendesk",
            "See Zendesk tickets on accounts and contacts, and open tickets from AIVA.",
            (
                "Every ticket from a contact or account, with status and priority, on the record",
                "Open a Zendesk ticket from an AIVA contact or case",
                "Ticket updates arrive through a signed webhook",
            ),
            "credentials", "helpdesk_sync",
            credential_fields=(
                CredentialField("subdomain", "Subdomain", help="The part before .zendesk.com.", placeholder="acme"),
                CredentialField("email", "Agent email", help="The Zendesk agent the API token belongs to."),
                CredentialField("api_token", "API token", secret=True, help="Admin Center → Apps and integrations → Zendesk API."),
            ),
            docs_url="https://developer.zendesk.com/api-reference/", color="#03363d", monogram="Z",
            setup=("Paste your subdomain, agent email and an API token.", "AIVA creates the webhook in Zendesk for you.", "Tickets appear on matching contacts and accounts."),
        ),
        IntegrationDef(
            "freshdesk", "Freshdesk", "Customer Support", "Freshworks",
            "Freshdesk tickets on AIVA records, and tickets created from AIVA.",
            (
                "Tickets from a contact or company shown on the AIVA record",
                "Create a Freshdesk ticket from a contact or case",
                "Status changes sync through a webhook",
            ),
            "credentials", "helpdesk_sync",
            credential_fields=(
                CredentialField("domain", "Domain", help="Your Freshdesk address, like acme.freshdesk.com.", placeholder="acme.freshdesk.com"),
                CredentialField("api_key", "API key", secret=True, help="Freshdesk → Profile settings → Your API key."),
            ),
            docs_url="https://developers.freshdesk.com/api/", color="#25c16f", monogram="Fd",
            setup=("Paste your domain and API key.", "Add the webhook URL in a Freshdesk automation rule.", "Tickets appear on matching contacts and accounts."),
        ),
        IntegrationDef(
            "intercom", "Intercom", "Customer Support", "Intercom",
            "Intercom conversations on AIVA contacts, and new Intercom leads in AIVA.",
            (
                "Conversations with a contact shown on the AIVA record",
                "Intercom leads and users matched to AIVA contacts and leads",
                "New conversations and replies arrive through a signed webhook",
            ),
            "credentials", "helpdesk_sync",
            credential_fields=(
                CredentialField("access_token", "Access token", secret=True, help="Intercom Developer Hub → your app → Authentication."),
                CredentialField("client_secret", "Client secret", secret=True, required=False, help="Developer Hub → Basic information. Used to check webhook signatures."),
                CredentialField("region", "Region", required=False, help="us, eu or au", placeholder="us"),
            ),
            docs_url="https://developers.intercom.com/docs/references/rest-api/api.intercom.io", color="#1f8ded", monogram="I",
            setup=("Paste an access token from your Intercom app.", "Add AIVA's webhook URL in the Developer Hub and paste the client secret.", "Conversations appear on matching contacts."),
        ),
        IntegrationDef(
            "help_scout", "Help Scout", "Customer Support", "Help Scout",
            "Help Scout conversations on AIVA contacts and accounts.",
            (
                "Conversations with a customer shown on the AIVA record",
                "Start a Help Scout conversation from a contact or case",
                "Updates arrive through a signed webhook",
            ),
            "credentials", "helpdesk_sync",
            credential_fields=(
                CredentialField("app_id", "App ID", help="Help Scout → Your profile → My apps → Create my app."),
                CredentialField("app_secret", "App secret", secret=True),
            ),
            docs_url="https://developer.helpscout.com/mailbox-api/", color="#1292ee", monogram="HS",
            setup=("Create an app in Help Scout and paste its ID and secret.", "AIVA registers its webhook for you.", "Conversations appear on matching contacts."),
        ),
        IntegrationDef(
            "jira_service_management", "Jira Service Management", "Customer Support", "Atlassian",
            "Jira Service Management requests on AIVA records, and escalation from AIVA cases.",
            (
                "Requests raised by a customer shown on the AIVA contact and account",
                "Escalate an AIVA case to a Jira request and follow its status",
                "Status changes arrive through a webhook",
            ),
            "credentials", "helpdesk_sync",
            credential_fields=(
                CredentialField("site", "Site", help="Your Atlassian site, like acme.atlassian.net.", placeholder="acme.atlassian.net"),
                CredentialField("email", "Atlassian account email"),
                CredentialField("api_token", "API token", secret=True, help="id.atlassian.com → Security → API tokens."),
                CredentialField("service_desk_id", "Service desk ID", required=False, help="Leave blank to pick from a list after connecting."),
            ),
            docs_url="https://developer.atlassian.com/cloud/jira/service-desk/rest/", color="#0c66e4", monogram="J",
            setup=("Paste your site, email and an API token.", "Pick the service desk and request type for escalations.", "Add AIVA's webhook in Jira for status updates."),
        ),
        # ------------------------------------------------------------ E-signature (more)
        IntegrationDef(
            "adobe_sign", "Adobe Acrobat Sign", "E-signature", "Adobe",
            "Send quotes and documents for signature with Adobe Acrobat Sign.",
            (
                "Send an AIVA quote or document to one or more signers in order",
                "Status on the record; the signed PDF is saved to Documents",
                "Signed quotes are marked accepted",
            ),
            "credentials", "e_signature",
            credential_fields=(
                CredentialField("integration_key", "Integration key", secret=True, help="Acrobat Sign → Account → Acrobat Sign API → API information → Integration key (agreement_read, agreement_write, agreement_send, webhook_read, webhook_write)."),
                CredentialField("api_access_point", "API access point", required=False, help="Leave blank and AIVA looks it up, like https://api.na1.adobesign.com/."),
            ),
            docs_url="https://developer.adobe.com/acrobat-sign/docs/overview/developer_guide/", color="#eb1000", monogram="AS",
            setup=("Paste an integration key.", "AIVA registers its webhook with Acrobat Sign.", "Send a quote for signature."),
        ),
        IntegrationDef(
            "pandadoc", "PandaDoc", "E-signature", "PandaDoc",
            "Create PandaDoc documents from AIVA quotes and templates and get them signed.",
            (
                "Create a document from a PandaDoc template, filled from the deal, account and contact",
                "Send quotes and AIVA documents for signature",
                "Viewed, completed and paid statuses on the record; the signed PDF saved to Documents",
            ),
            "credentials", "e_signature",
            credential_fields=(
                CredentialField("api_key", "API key", secret=True, help="PandaDoc → Settings → Integrations → API → generate a production API key."),
            ),
            docs_url="https://developers.pandadoc.com/reference/about", color="#25b07c", monogram="PD",
            setup=("Paste an API key.", "AIVA subscribes to document updates for you.", "Create a document from a template on a deal."),
        ),
        # ------------------------------------------------------------ Sales intelligence
        IntegrationDef(
            "apollo", "Apollo.io", "Sales Intelligence", "Apollo",
            "Enrich contacts and accounts from Apollo and import prospects.",
            (
                "Fill in titles, emails, phones and company data from Apollo",
                "Apollo joins the waterfall enrichment order",
                "Search Apollo's people and companies and import them as leads or contacts",
            ),
            "credentials", "waterfall_enrichment",
            credential_fields=(
                CredentialField("api_key", "API key", secret=True, help="Apollo → Settings → Integrations → API → Create new key (a master key is needed for people search)."),
            ),
            docs_url="https://docs.apollo.io/reference/", color="#f9d33f", monogram="A",
            setup=("Paste an API key.", "Place Apollo in the enrichment order.", "Search for prospects and import them."),
        ),
        IntegrationDef(
            "zoominfo", "ZoomInfo", "Sales Intelligence", "ZoomInfo",
            "Enrich records with ZoomInfo data and import prospects.",
            (
                "Fill in contact and company data from ZoomInfo",
                "ZoomInfo joins the waterfall enrichment order",
                "Search ZoomInfo contacts and companies and import them",
            ),
            "credentials", "waterfall_enrichment",
            credential_fields=(
                CredentialField("username", "Username", help="Your ZoomInfo API username."),
                CredentialField("client_id", "Client ID", help="ZoomInfo Admin Portal → API → your PKI app."),
                CredentialField("private_key", "Private key", secret=True, help="The PKI private key (PEM) for that app."),
            ),
            docs_url="https://api-docs.zoominfo.com/", color="#e3283b", monogram="Zi",
            setup=("Paste your API username, client ID and private key.", "Place ZoomInfo in the enrichment order.", "Search for prospects and import them."),
        ),
        # ------------------------------------------------------------ Lead capture (more)
        IntegrationDef(
            "linkedin_lead_gen", "LinkedIn Lead Gen Forms", "Lead Capture", "LinkedIn",
            "Leads from LinkedIn Lead Gen Forms arrive in AIVA automatically.",
            (
                "Every lead-form submission on your ad accounts becomes an AIVA lead",
                "Map form questions to lead fields",
                "Campaign and form names are kept on the lead for reporting",
            ),
            "oauth", "web_forms", family="linkedin",
            scopes=("openid", "profile", "email", "r_marketing_leadgen_automation", "r_ads", "r_organization_admin"),
            docs_url="https://learn.microsoft.com/linkedin/marketing/lead-sync/leadsync", color="#0a66c2", monogram="in",
            setup=("Click Connect and sign in with a LinkedIn admin of your ad account.", "Choose the ad accounts and forms to sync.", "Leads arrive within minutes."),
        ),
        # ------------------------------------------------------------ Analytics
        IntegrationDef(
            "google_analytics", "Google Analytics 4", "Analytics", "Google",
            "Send CRM conversions to Google Analytics 4 and see where leads came from.",
            (
                "Lead created, deal won and other CRM events sent to GA4 as conversions, with their value",
                "The visitor's GA client ID is kept on leads from your forms and website",
                "Report on revenue by channel and campaign in GA4",
            ),
            "credentials", "bi_connectors",
            credential_fields=(
                CredentialField("measurement_id", "Measurement ID", help="GA4 → Admin → Data streams → your web stream.", placeholder="G-XXXXXXXXXX"),
                CredentialField("api_secret", "Measurement Protocol API secret", secret=True, help="Same data stream → Measurement Protocol API secrets → Create."),
            ),
            docs_url="https://developers.google.com/analytics/devguides/collection/protocol/ga4", color="#e37400", monogram="GA",
            setup=("Paste the measurement ID and an API secret.", "Pick the CRM events to send and their values.", "Mark them as key events in GA4."),
        ),
        IntegrationDef(
            "power_bi", "Microsoft Power BI", "Analytics", "Microsoft",
            "Build Power BI reports on live AIVA data.",
            (
                "A read-only data feed of leads, contacts, accounts, deals, activities, cases and invoices",
                "Ready-made Power Query to paste into Power BI Desktop",
                "Scheduled refresh in the Power BI service with an API key",
            ),
            "api_key", "bi_connectors",
            docs_url="https://learn.microsoft.com/power-bi/connect-data/desktop-connect-to-web", color="#f2c811", monogram="BI",
            setup=("Create a read-only API key for Power BI.", "In Power BI Desktop: Get data → Blank query → paste the query from this page.", "Publish and set a scheduled refresh."),
        ),
        IntegrationDef(
            "looker_studio", "Looker Studio", "Analytics", "Google",
            "Build Looker Studio dashboards on AIVA data.",
            (
                "A read-only data feed of leads, contacts, accounts, deals, activities, cases and invoices",
                "A ready-made Looker Studio community connector",
                "Or push the data to a Google Sheet that refreshes on a schedule",
            ),
            "api_key", "bi_connectors",
            docs_url="https://developers.google.com/looker-studio/connector", color="#4285f4", monogram="LS",
            setup=("Create a read-only API key for Looker Studio.", "Add the AIVA community connector from this page.", "Pick a dataset and build your report."),
        ),
        # ------------------------------------------------------------ Identity & access
        IntegrationDef(
            "entra_id", "Microsoft Entra ID", "Identity & Access", "Microsoft",
            "Single sign-on with Entra ID and automatic user provisioning (SCIM).",
            (
                "People sign in to AIVA with their Microsoft work account (OpenID Connect)",
                "Users are created, updated and deactivated from Entra ID through SCIM 2.0",
                "Entra groups map to AIVA roles and teams",
                "Optionally require single sign-on for everyone on your verified domains",
            ),
            "credentials", "sso",
            credential_fields=(
                CredentialField("tenant_id", "Directory (tenant) ID", help="Entra admin center → Overview.", placeholder="00000000-0000-0000-0000-000000000000"),
                CredentialField("client_id", "Application (client) ID", required=False, help="Optional: an app registration with Microsoft Graph Group.Read.All and User.Read.All (application) so AIVA can list groups for mapping."),
                CredentialField("client_secret", "Client secret", secret=True, required=False),
            ),
            docs_url="https://learn.microsoft.com/entra/identity/app-provisioning/use-scim-to-provision-users-and-groups", color="#0078d4", monogram="E",
            setup=("Enter your tenant ID.", "Set up single sign-on with the values on this page.", "Create a SCIM token and add AIVA as an enterprise application with provisioning."),
        ),
        IntegrationDef(
            "okta", "Okta", "Identity & Access", "Okta",
            "Okta single sign-on and lifecycle management (SCIM) for AIVA users.",
            (
                "People sign in to AIVA through Okta (OpenID Connect)",
                "Users are created, updated and deactivated from Okta through SCIM 2.0",
                "Okta groups map to AIVA roles and teams (group push)",
                "Optionally require single sign-on for everyone on your verified domains",
            ),
            "credentials", "sso",
            credential_fields=(
                CredentialField("okta_domain", "Okta domain", help="Your Okta org address, like acme.okta.com.", placeholder="acme.okta.com"),
                CredentialField("api_token", "API token", secret=True, required=False, help="Optional: Security → API → Tokens (read-only admin), so AIVA can list groups for mapping."),
            ),
            docs_url="https://developer.okta.com/docs/guides/scim-provisioning-integration-overview/main/", color="#00297a", monogram="O",
            setup=("Enter your Okta domain.", "Set up single sign-on with the values on this page.", "Create a SCIM token and turn on provisioning in your Okta app."),
        ),
        # ------------------------------------------------------------ AI models
        IntegrationDef(
            "openai", "OpenAI API", "AI Models", "OpenAI",
            "Run AIVA's AI features on your own OpenAI account and models.",
            (
                "Summaries, drafts, Ask AIVA, AI agents, call notes and field prompts use the models you choose",
                "Pick a model per feature; usage and cost per feature and person",
                "Your own key, organization and project, with spending and data controls",
            ),
            "credentials", "ai_models",
            credential_fields=(
                CredentialField("api_key", "API key", secret=True, help="platform.openai.com → API keys. A project key is recommended.", placeholder="sk-proj-…"),
                CredentialField("organization", "Organization ID", required=False, placeholder="org-…"),
                CredentialField("project", "Project ID", required=False, placeholder="proj_…"),
            ),
            docs_url="https://platform.openai.com/docs/api-reference", color="#10a37f", monogram="AI",
            setup=("Paste an API key.", "Choose the model for each AI feature.", "Set usage limits and data controls, then send a test prompt."),
        ),
        IntegrationDef(
            "azure_openai", "Azure OpenAI", "AI Models", "Microsoft",
            "Run AIVA's AI features on your Azure OpenAI deployments.",
            (
                "Your models in your Azure tenant and region, under your data and compliance commitments",
                "Map each AI feature to one of your deployments",
                "API key or Microsoft Entra ID authentication",
            ),
            "credentials", "ai_models",
            credential_fields=(
                CredentialField("endpoint", "Endpoint", help="Azure portal → your Azure OpenAI resource → Keys and Endpoint.", placeholder="https://your-resource.openai.azure.com"),
                CredentialField("api_key", "API key", secret=True, required=False, help="Key 1 or Key 2. Leave blank to use Entra ID below."),
                CredentialField("tenant_id", "Entra tenant ID", required=False, help="For Entra ID authentication instead of a key."),
                CredentialField("client_id", "Entra client ID", required=False),
                CredentialField("client_secret", "Entra client secret", secret=True, required=False, help="The app needs the Cognitive Services OpenAI User role on the resource."),
            ),
            docs_url="https://learn.microsoft.com/azure/ai-services/openai/reference", color="#0078d4", monogram="Az",
            setup=("Paste the endpoint and a key (or Entra app details).", "Map each AI feature to a deployment.", "Send a test prompt."),
        ),
        # ------------------------------------------------------------ ERP
        IntegrationDef(
            "netsuite", "Oracle NetSuite", "ERP", "Oracle",
            "Customers, items, sales orders, invoices and payments with NetSuite.",
            (
                "Accounts become NetSuite customers; contacts follow",
                "Items and prices come from NetSuite; stock levels on products",
                "Won deals and accepted quotes become NetSuite sales orders",
                "Invoices and payments flow back; open balance and overdue amounts on the account",
            ),
            "credentials", "erp_sync",
            credential_fields=(
                CredentialField("account_id", "Account ID", help="Setup → Company → Company Information → Account ID (sandbox IDs end in _SB1).", placeholder="1234567 or 1234567_SB1"),
                CredentialField("consumer_key", "Consumer key", secret=True, help="Setup → Integration → Manage Integrations → your integration (token-based authentication)."),
                CredentialField("consumer_secret", "Consumer secret", secret=True),
                CredentialField("token_id", "Token ID", secret=True, help="Setup → Users/Roles → Access Tokens → New."),
                CredentialField("token_secret", "Token secret", secret=True),
            ),
            docs_url="https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/book_1559132836.html", color="#c74634", monogram="NS",
            setup=("Create an integration with token-based authentication and an access token for an integration role.", "Paste the account ID, consumer and token credentials.", "Pick the subsidiary, items and what syncs."),
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

CATEGORIES = (
    "Email", "Calendar", "Communication", "Meetings", "Scheduling", "Email Marketing", "Lead Capture", "Advertising",
    "Sales Intelligence", "Documents", "E-signature", "Accounting", "ERP", "Payments", "Customer Support", "Analytics",
    "Identity & Access", "AI Models", "Automation",
)


def get(key: str) -> IntegrationDef | None:
    return INTEGRATIONS.get(key)
