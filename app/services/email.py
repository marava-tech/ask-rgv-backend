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
<title>You&rsquo;re in — Ask RGV</title>
</head>
<body style="margin:0;padding:0;background:#F0EEE9;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;color:#111111;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#F0EEE9;padding:40px 16px;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#FAFAF8;border:1px solid #E2E0DB;">

  <!-- Top gold rule -->
  <tr>
    <td style="height:4px;background:#C8972A;font-size:0;line-height:0;">&nbsp;</td>
  </tr>

  <!-- Header -->
  <tr>
    <td style="padding:36px 40px 28px;border-bottom:1px solid #E2E0DB;">
      <p style="margin:0 0 6px;font-size:10px;letter-spacing:0.28em;text-transform:uppercase;color:#999999;font-family:monospace;">Ask RGV &mdash; Waitlist Confirmed</p>
      <h1 style="margin:0;font-size:36px;font-weight:800;color:#111111;letter-spacing:0.02em;line-height:1.1;">You&rsquo;re in,<br/>{first_name}.</h1>
    </td>
  </tr>

  <!-- Intro -->
  <tr>
    <td style="padding:28px 40px 0;">
      <p style="margin:0;font-size:15px;line-height:1.7;color:#444444;">
        Your spot on the Ask RGV waitlist is locked. Below are your personal discount codes &mdash; save them, you&rsquo;ll need them at launch.
      </p>
    </td>
  </tr>

  <!-- App promo code -->
  <tr>
    <td style="padding:24px 40px 0;">
      <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #E2E0DB;background:#FAFAF8;">
        <tr>
          <td style="padding:4px 20px;background:#C8972A;">
            <p style="margin:0;font-size:9px;letter-spacing:0.25em;text-transform:uppercase;color:#000000;font-family:monospace;font-weight:700;">App Subscription Code</p>
          </td>
        </tr>
        <tr>
          <td style="padding:18px 20px 16px;">
            <p style="margin:0 0 4px;font-size:26px;font-weight:700;color:#C8972A;letter-spacing:0.14em;font-family:monospace;">{app_code}</p>
            <p style="margin:0;font-size:12px;color:#888888;line-height:1.5;">10% off any plan &mdash; locked forever at your signup price</p>
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- Merch promo code -->
  <tr>
    <td style="padding:12px 40px 0;">
      <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #E2E0DB;background:#FAFAF8;">
        <tr>
          <td style="padding:4px 20px;background:#3A3A3A;">
            <p style="margin:0;font-size:9px;letter-spacing:0.25em;text-transform:uppercase;color:#CCCCCC;font-family:monospace;font-weight:700;">Merchandise Code</p>
          </td>
        </tr>
        <tr>
          <td style="padding:18px 20px 16px;">
            <p style="margin:0 0 4px;font-size:26px;font-weight:700;color:#444444;letter-spacing:0.14em;font-family:monospace;">{merch_code}</p>
            <p style="margin:0;font-size:12px;color:#888888;line-height:1.5;">20% off your first merch order</p>
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- What happens next -->
  <tr>
    <td style="padding:32px 40px 0;">
      <p style="margin:0 0 16px;font-size:10px;letter-spacing:0.25em;text-transform:uppercase;color:#999999;font-family:monospace;">What happens next</p>
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td style="padding:12px 0;border-top:1px solid #E2E0DB;">
            <p style="margin:0;font-size:14px;color:#555555;line-height:1.6;">
              <span style="color:#C8972A;font-weight:700;margin-right:10px;">&rarr;</span>
              We&rsquo;ll email you the moment the Android app goes live on Google Play.
            </p>
          </td>
        </tr>
        <tr>
          <td style="padding:12px 0;border-top:1px solid #E2E0DB;">
            <p style="margin:0;font-size:14px;color:#555555;line-height:1.6;">
              <span style="color:#C8972A;font-weight:700;margin-right:10px;">&rarr;</span>
              That launch email has your Play Store link &mdash; download and use your app code at checkout.
            </p>
          </td>
        </tr>
        <tr>
          <td style="padding:12px 0;border-top:1px solid #E2E0DB;">
            <p style="margin:0;font-size:14px;color:#555555;line-height:1.6;">
              <span style="color:#C8972A;font-weight:700;margin-right:10px;">&rarr;</span>
              Merch drops alongside the app. Use your merch code at the store &mdash; ships within 7 days.
            </p>
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- RGV quote -->
  <tr>
    <td style="padding:28px 40px 0;">
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#F3F2EE;border-left:3px solid #C8972A;">
        <tr>
          <td style="padding:16px 20px;">
            <p style="margin:0 0 8px;font-size:14px;line-height:1.7;color:#555555;font-style:italic;">
              &ldquo;Fear of failure is worse than failure itself. The point is, even your failures are more interesting than other people&rsquo;s successes.&rdquo;
            </p>
            <p style="margin:0;font-size:11px;color:#C8972A;letter-spacing:0.1em;font-weight:700;">— RGV</p>
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- CTA -->
  <tr>
    <td style="padding:28px 40px 32px;">
      <p style="margin:0 0 14px;font-size:13px;color:#888888;">Know someone who&rsquo;d love RGV&rsquo;s unfiltered take? Share the waitlist:</p>
      <a href="{_WAITLIST_URL}" style="display:inline-block;background:#C8972A;color:#000000;font-size:11px;font-weight:700;letter-spacing:0.18em;text-transform:uppercase;text-decoration:none;padding:13px 28px;">{_WAITLIST_URL} &rarr;</a>
    </td>
  </tr>

  <!-- Footer -->
  <tr>
    <td style="padding:20px 40px;border-top:1px solid #E2E0DB;background:#F3F2EE;">
      <p style="margin:0;font-size:11px;color:#AAAAAA;line-height:1.6;">
        You received this because you signed up at askrgv.marava.tech.<br/>
        No spam &mdash; only your launch notification and these codes.
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
    subject = f"[ASK RGV AI] You're in. Here are your codes, {first_name}."

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
