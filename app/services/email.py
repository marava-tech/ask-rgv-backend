import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from core.config import settings

logger = logging.getLogger(__name__)

_WAITLIST_URL = "https://askrgv.marava.tech"


def _build_html(name: str, app_code: str, merch_code: str) -> str:
    first_name = name.split()[0] if name else name
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>You're in — Ask RGV</title>
</head>
<body style="margin:0;padding:0;background:#0a0a0a;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;color:#d4d4d4;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0a0a;padding:40px 20px;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#111;border:1px solid #222;">

        <!-- Header -->
        <tr>
          <td style="padding:36px 40px 28px;border-bottom:1px solid #1e1e1e;">
            <p style="margin:0 0 4px;font-size:11px;letter-spacing:0.25em;text-transform:uppercase;color:#888;font-family:monospace;">Ask RGV</p>
            <h1 style="margin:0;font-size:32px;font-weight:700;color:#f5f5f5;letter-spacing:0.04em;">YOU&rsquo;RE IN.</h1>
          </td>
        </tr>

        <!-- Greeting -->
        <tr>
          <td style="padding:28px 40px 0;">
            <p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:#aaa;">
              Hey {first_name},
            </p>
            <p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:#aaa;">
              Your spot on the Ask RGV waitlist is confirmed. Here are your personal discount codes — keep them safe.
            </p>
          </td>
        </tr>

        <!-- App promo code -->
        <tr>
          <td style="padding:20px 40px 0;">
            <table width="100%" cellpadding="0" cellspacing="0" style="background:#1a1400;border:1px solid #c8972a44;padding:20px 24px;">
              <tr>
                <td>
                  <p style="margin:0 0 6px;font-size:10px;letter-spacing:0.2em;text-transform:uppercase;color:#c8972a;font-family:monospace;">App Subscription</p>
                  <p style="margin:0 0 6px;font-size:22px;font-weight:700;color:#e8b84b;letter-spacing:0.12em;font-family:monospace;">{app_code}</p>
                  <p style="margin:0;font-size:12px;color:#888;">10% off any plan — locked forever at signup price</p>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Merch promo code -->
        <tr>
          <td style="padding:12px 40px 0;">
            <table width="100%" cellpadding="0" cellspacing="0" style="background:#0d1a0d;border:1px solid #4a9a4a44;padding:20px 24px;">
              <tr>
                <td>
                  <p style="margin:0 0 6px;font-size:10px;letter-spacing:0.2em;text-transform:uppercase;color:#5ab85a;font-family:monospace;">Merchandise</p>
                  <p style="margin:0 0 6px;font-size:22px;font-weight:700;color:#7acc7a;letter-spacing:0.12em;font-family:monospace;">{merch_code}</p>
                  <p style="margin:0;font-size:12px;color:#888;">20% off your first merch order</p>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- What happens next -->
        <tr>
          <td style="padding:28px 40px 0;">
            <p style="margin:0 0 12px;font-size:11px;letter-spacing:0.2em;text-transform:uppercase;color:#555;font-family:monospace;">What happens next</p>
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="padding:8px 0;border-top:1px solid #1e1e1e;">
                  <p style="margin:0;font-size:13px;color:#888;line-height:1.5;">
                    <span style="color:#c8972a;margin-right:8px;">→</span>
                    We&rsquo;ll email you the moment the Android app goes live on Google Play.
                  </p>
                </td>
              </tr>
              <tr>
                <td style="padding:8px 0;border-top:1px solid #1e1e1e;">
                  <p style="margin:0;font-size:13px;color:#888;line-height:1.5;">
                    <span style="color:#c8972a;margin-right:8px;">→</span>
                    That launch email will have your Play Store link — download and use your app code at checkout.
                  </p>
                </td>
              </tr>
              <tr>
                <td style="padding:8px 0;border-top:1px solid #1e1e1e;">
                  <p style="margin:0;font-size:13px;color:#888;line-height:1.5;">
                    <span style="color:#c8972a;margin-right:8px;">→</span>
                    Merch drops alongside the app. Use your merch code at the store — ships within 7 days of ordering.
                  </p>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Share -->
        <tr>
          <td style="padding:28px 40px;">
            <p style="margin:0 0 12px;font-size:13px;color:#666;line-height:1.5;">
              Know someone who'd love RGV&rsquo;s unfiltered take on cinema, life, and everything? Share the waitlist:
            </p>
            <a href="{_WAITLIST_URL}" style="display:inline-block;background:#c8972a;color:#000;font-size:12px;font-weight:700;letter-spacing:0.15em;text-transform:uppercase;text-decoration:none;padding:12px 24px;">{_WAITLIST_URL}</a>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="padding:20px 40px;border-top:1px solid #1e1e1e;">
            <p style="margin:0;font-size:11px;color:#444;line-height:1.6;">
              You received this because you signed up at askrgv.marava.tech. No spam — just launch news and your codes.
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


async def send_waitlist_confirmation(name: str, email: str, app_code: str, merch_code: str) -> None:
    if not settings.email_enabled:
        return

    first_name = name.split()[0] if name else name
    subject = f"You're in. Here are your codes, {first_name}."

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.email_from
    msg["To"] = email

    html_body = _build_html(name, app_code, merch_code)
    plain_body = (
        f"Hey {first_name},\n\n"
        f"You're on the Ask RGV waitlist!\n\n"
        f"App code (10% off any plan): {app_code}\n"
        f"Merch code (20% off first order): {merch_code}\n\n"
        f"We'll email you at launch with your Play Store link.\n\n"
        f"— Ask RGV team"
    )

    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.email_host,
            port=settings.email_port,
            username=settings.email_username,
            password=settings.email_password,
            start_tls=True,
            timeout=10,
        )
    except Exception as exc:
        logger.error("EMAIL_SEND_FAILURE to=%s error=%s", email, exc)
