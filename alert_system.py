

"""
Alert System for Trading Bot
Supports: Telegram, Discord, Email, Desktop notifications, and Sound alerts
"""

import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import json
from datetime import datetime
import time
from collections import deque
import os

try:
    from plyer import notification  # For desktop notifications
    DESKTOP_NOTIFICATIONS_AVAILABLE = True
except ImportError:
    DESKTOP_NOTIFICATIONS_AVAILABLE = False

try:
    import winsound  # For Windows sound alerts
    SOUND_AVAILABLE = True
except ImportError:
    SOUND_AVAILABLE = False


class AlertSystem:
    """
    Multi-channel alert system for trading signals
    """
    
    def _init_(self, config=None):
        """
        Initialize alert system with configuration
        
        config = {
            "telegram": {
                "enabled": True,
                "bot_token": "YOUR_BOT_TOKEN",
                "chat_id": "YOUR_CHAT_ID"
            },
            "discord": {
                "enabled": False,
                "webhook_url": "YOUR_DISCORD_WEBHOOK"
            },
            "email": {
                "enabled": False,
                "smtp_server": "smtp.gmail.com",
                "smtp_port": 587,
                "sender_email": "your_email@gmail.com",
                "sender_password": "your_app_password",
                "recipient_email": "recipient@gmail.com"
            },
            "desktop": {
                "enabled": True
            },
            "sound": {
                "enabled": True,
                "buy_frequency": 1000,  # Hz
                "sell_frequency": 500,  # Hz
                "duration": 200  # ms
            },
            "alert_cooldown": 60,  # seconds between same type alerts
            "min_confidence": 80  # Only alert on signals >= 80% confidence
        }
        """
        self.config = config or self._default_config()
        self.alert_history = deque(maxlen=100)
        self.last_alert_time = {}
        
    def _default_config(self):
        """Default configuration"""
        return {
            "telegram": {"enabled": False},
            "discord": {"enabled": False},
            "email": {"enabled": False},
            "desktop": {"enabled": True},
            "sound": {"enabled": True, "buy_frequency": 1000, "sell_frequency": 500, "duration": 200},
            "alert_cooldown": 60,
            "min_confidence": 80
        }
    
    def should_alert(self, alert_type, confidence):
        """Check if alert should be sent based on cooldown and confidence"""
        
        # Check confidence threshold
        if confidence < self.config.get("min_confidence", 80):
            return False
        
        # Check cooldown
        current_time = time.time()
        last_time = self.last_alert_time.get(alert_type, 0)
        cooldown = self.config.get("alert_cooldown", 60)
        
        if current_time - last_time < cooldown:
            return False
        
        return True
    
    def send_alert(self, signal_type, confidence, details=None):
        """
        Send alert through all enabled channels
        
        signal_type: "BUY", "SELL", "MOMENTUM_LOSS", "LIQUIDITY_GRAB"
        confidence: 0-100
        details: dict with additional info
        """
        
        if not self.should_alert(signal_type, confidence):
            return False
        
        # Update last alert time
        self.last_alert_time[signal_type] = time.time()
        
        # Prepare alert message
        message = self._format_message(signal_type, confidence, details)
        
        # Log alert
        self.alert_history.append({
            "timestamp": datetime.now(),
            "type": signal_type,
            "confidence": confidence,
            "message": message
        })
        
        # Send through all enabled channels
        results = {}
        
        if self.config.get("telegram", {}).get("enabled"):
            results["telegram"] = self._send_telegram(message, signal_type)
        
        if self.config.get("discord", {}).get("enabled"):
            results["discord"] = self._send_discord(message, signal_type)
        
        if self.config.get("email", {}).get("enabled"):
            results["email"] = self._send_email(message, signal_type, details)
        
        if self.config.get("desktop", {}).get("enabled") and DESKTOP_NOTIFICATIONS_AVAILABLE:
            results["desktop"] = self._send_desktop(message, signal_type)
        
        if self.config.get("sound", {}).get("enabled") and SOUND_AVAILABLE:
            results["sound"] = self._play_sound(signal_type)
        
        return results
    
    def _format_message(self, signal_type, confidence, details):
        """Format alert message"""
        
        emoji_map = {
            "BUY": "🚀",
            "SELL": "🔻",
            "MOMENTUM_LOSS": "⚠️",
            "LIQUIDITY_GRAB": "🎯"
        }
        
        emoji = emoji_map.get(signal_type, "📊")
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        message = f"{emoji} *{signal_type} SIGNAL*\n"
        message += f"⏰ Time: {timestamp}\n"
        message += f"📊 Confidence: {confidence}%\n"
        
        if details:
            if "price" in details:
                message += f"💰 Price: ${details['price']:,.2f}\n"
            
            if "volume_zscore" in details:
                message += f"📈 Volume: {details['volume_zscore']:.1f}σ\n"
            
            if "delta_ratio" in details:
                message += f"⚖️ Delta: {details['delta_ratio']:.1%}\n"
            
            if "whale_concentration" in details:
                message += f"🐳 Whales: {details['whale_concentration']:.1%}\n"
            
            if "momentum_loss_types" in details:
                message += f"\n⚠️ Loss Types: {', '.join(details['momentum_loss_types'])}\n"
        
        return message
    
    def _send_telegram(self, message, signal_type):
        """Send Telegram alert"""
        try:
            config = self.config["telegram"]
            bot_token = config.get("bot_token")
            chat_id = config.get("chat_id")
            
            if not bot_token or not chat_id:
                return {"success": False, "error": "Missing config"}
            
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            
            payload = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown"
            }
            
            response = requests.post(url, json=payload, timeout=5)
            
            if response.status_code == 200:
                return {"success": True}
            else:
                return {"success": False, "error": response.text}
                
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _send_discord(self, message, signal_type):
        """Send Discord webhook alert"""
        try:
            config = self.config["discord"]
            webhook_url = config.get("webhook_url")
            
            if not webhook_url:
                return {"success": False, "error": "Missing webhook URL"}
            
            # Color based on signal type
            color_map = {
                "BUY": 0x00ff00,      # Green
                "SELL": 0xff0000,     # Red
                "MOMENTUM_LOSS": 0xffaa00,  # Orange
                "LIQUIDITY_GRAB": 0x9b59b6  # Purple
            }
            
            payload = {
                "embeds": [{
                    "title": f"{signal_type} Signal",
                    "description": message,
                    "color": color_map.get(signal_type, 0x3498db),
                    "timestamp": datetime.utcnow().isoformat()
                }]
            }
            
            response = requests.post(webhook_url, json=payload, timeout=5)
            
            if response.status_code == 204:
                return {"success": True}
            else:
                return {"success": False, "error": response.text}
                
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _send_email(self, message, signal_type, details):
        """Send email alert"""
        try:
            config = self.config["email"]
            
            msg = MIMEMultipart()
            msg['From'] = config.get("sender_email")
            msg['To'] = config.get("recipient_email")
            msg['Subject'] = f"Trading Alert: {signal_type} Signal"
            
            body = message
            if details:
                body += f"\n\nFull Details:\n{json.dumps(details, indent=2)}"
            
            msg.attach(MIMEText(body, 'plain'))
            
            server = smtplib.SMTP(config.get("smtp_server"), config.get("smtp_port"))
            server.starttls()
            server.login(config.get("sender_email"), config.get("sender_password"))
            server.send_message(msg)
            server.quit()
            
            return {"success": True}
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _send_desktop(self, message, signal_type):
        """Send desktop notification"""
        try:
            title_map = {
                "BUY": "🚀 Buy Signal",
                "SELL": "🔻 Sell Signal",
                "MOMENTUM_LOSS": "⚠️ Momentum Loss",
                "LIQUIDITY_GRAB": "🎯 Liquidity Grab"
            }
            
            notification.notify(
                title=title_map.get(signal_type, "Trading Alert"),
                message=message.replace("*", "").replace("", ""),
                app_name="Trading Bot",
                timeout=10
            )
            
            return {"success": True}
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _play_sound(self, signal_type):
        """Play sound alert (Windows only)"""
        try:
            config = self.config["sound"]
            
            if signal_type == "BUY":
                frequency = config.get("buy_frequency", 1000)
            elif signal_type == "SELL":
                frequency = config.get("sell_frequency", 500)
            else:
                frequency = 750
            
            duration = config.get("duration", 200)
            
            # Play beep
            winsound.Beep(frequency, duration)
            
            return {"success": True}
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def get_alert_stats(self):
        """Get statistics about sent alerts"""
        if not self.alert_history:
            return {"total_alerts": 0}
        
        total = len(self.alert_history)
        by_type = {}
        
        for alert in self.alert_history:
            alert_type = alert["type"]
            by_type[alert_type] = by_type.get(alert_type, 0) + 1
        
        return {
            "total_alerts": total,
            "by_type": by_type,
            "last_alert": self.alert_history[-1] if self.alert_history else None
        }


# ========== USAGE EXAMPLE ==========
"""
# 1. CREATE CONFIG FILE (config.json)
{
    "telegram": {
        "enabled": true,
        "bot_token": "YOUR_BOT_TOKEN_HERE",
        "chat_id": "YOUR_CHAT_ID_HERE"
    },
    "discord": {
        "enabled": false,
        "webhook_url": "YOUR_DISCORD_WEBHOOK_URL"
    },
    "email": {
        "enabled": false,
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "sender_email": "your_email@gmail.com",
        "sender_password": "your_app_password",
        "recipient_email": "recipient@gmail.com"
    },
    "desktop": {
        "enabled": true
    },
    "sound": {
        "enabled": true,
        "buy_frequency": 1000,
        "sell_frequency": 500,
        "duration": 200
    },
    "alert_cooldown": 60,
    "min_confidence": 80
}

# 2. IN YOUR DASHBOARD CODE
import json
from alert_system import AlertSystem

# Load config
with open('alert_config.json', 'r') as f:
    alert_config = json.load(f)

# Initialize alert system
if "alert_system" not in st.session_state:
    st.session_state.alert_system = AlertSystem(alert_config)

# 3. SEND ALERTS WHEN SIGNALS APPEAR
aggressive_data = st.session_state.enhanced_detector.detect_clean_aggressive_move(...)

# On BUY signal
if aggressive_data["aggressive_buy"] and aggressive_data["confidence"] >= 80:
    st.session_state.alert_system.send_alert(
        signal_type="BUY",
        confidence=aggressive_data["confidence"],
        details={
            "price": df.iloc[0]["price"],
            "volume_zscore": aggressive_data["metrics"]["volume_zscore"],
            "delta_ratio": aggressive_data["metrics"]["delta_ratio"],
            "whale_concentration": aggressive_data["metrics"]["whale_concentration"]
        }
    )

# On SELL signal
if aggressive_data["aggressive_sell"] and aggressive_data["confidence"] >= 80:
    st.session_state.alert_system.send_alert(
        signal_type="SELL",
        confidence=aggressive_data["confidence"],
        details={
            "price": df.iloc[0]["price"],
            "volume_zscore": aggressive_data["metrics"]["volume_zscore"],
            "delta_ratio": aggressive_data["metrics"]["delta_ratio"],
            "whale_concentration": aggressive_data["metrics"]["whale_concentration"]
        }
    )

# On MOMENTUM LOSS
momentum_loss = aggressive_data.get("momentum_loss", {})
if momentum_loss.get("momentum_loss") and momentum_loss.get("severity") >= 70:
    st.session_state.alert_system.send_alert(
        signal_type="MOMENTUM_LOSS",
        confidence=momentum_loss["severity"],
        details={
            "momentum_loss_types": momentum_loss.get("loss_type", []),
            "severity": momentum_loss["severity"]
        }
    )
"""


# ========== HOW TO GET TELEGRAM BOT TOKEN ==========
"""
TELEGRAM SETUP (Easiest and Most Popular):

1. Open Telegram and search for @BotFather
2. Send /newbot command
3. Give your bot a name (e.g., "My Trading Alerts")
4. Give your bot a username (e.g., "mytradingalerts_bot")
5. BotFather will give you a TOKEN - copy this

6. Get your Chat ID:
   - Search for @userinfobot in Telegram
   - Send /start to it
   - It will reply with your Chat ID - copy this

7. Start a chat with your new bot:
   - Search for your bot username
   - Click START

8. Add to config:
   {
       "telegram": {
           "enabled": true,
           "bot_token": "123456789:ABCdefGHIjklMNOpqrsTUVwxyz",
           "chat_id": "987654321"
       }
   }

DONE! You'll receive Telegram messages for all signals.
"""


# ========== HOW TO GET DISCORD WEBHOOK ==========
"""
DISCORD SETUP:

1. Go to your Discord server
2. Right-click the channel where you want alerts
3. Click "Edit Channel"
4. Go to "Integrations"
5. Click "Create Webhook"
6. Give it a name (e.g., "Trading Bot")
7. Copy the webhook URL

8. Add to config:
   {
       "discord": {
           "enabled": true,
           "webhook_url": "https://discord.com/api/webhooks/..."
       }
   }
"""


# ========== EMAIL SETUP (Gmail) ==========
"""
GMAIL SETUP:

1. Enable 2-Step Verification on your Google account
2. Go to https://myaccount.google.com/apppasswords
3. Create an "App Password" for "Mail"
4. Copy the 16-character password

5. Add to config:
   {
       "email": {
           "enabled": true,
           "smtp_server": "smtp.gmail.com",
           "smtp_port": 587,
           "sender_email": "your_email@gmail.com",
           "sender_password": "your_16_char_app_password",
           "recipient_email": "where_to_send@gmail.com"
       }
   }
"""