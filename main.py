import asyncio
import json
import hmac
import hashlib
import time
import os
from datetime import datetime
import aiohttp
from aiohttp import web, ClientSession
import logging

# Configuration via variables d'environnement (plus sécurisé)
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID", "votre_client_id_ici")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET", "votre_client_secret_ici")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "votre_secret_webhook_ici")
CALLBACK_URL = os.getenv("CALLBACK_URL", "https://votre-app.onrender.com/webhook")
BROADCASTER_USER_LOGIN = os.getenv("BROADCASTER_USER_LOGIN", "nom_du_streamer")

# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TwitchEventSub:
    def __init__(self):
        self.access_token = None
        self.broadcaster_user_id = None
        self.session = None
        
    async def get_app_access_token(self):
        """Obtient un token d'accès d'application"""
        url = "https://id.twitch.tv/oauth2/token"
        params = {
            "client_id": TWITCH_CLIENT_ID,
            "client_secret": TWITCH_CLIENT_SECRET,
            "grant_type": "client_credentials"
        }
        
        async with self.session.post(url, params=params) as response:
            if response.status == 200:
                data = await response.json()
                self.access_token = data["access_token"]
                logger.info("Token d'accès obtenu avec succès")
                return True
            else:
                logger.error(f"Erreur lors de l'obtention du token: {response.status}")
                return False
    
    async def get_broadcaster_id(self):
        """Récupère l'ID du broadcaster à partir de son nom d'utilisateur"""
        url = "https://api.twitch.tv/helix/users"
        headers = {
            "Client-Id": TWITCH_CLIENT_ID,
            "Authorization": f"Bearer {self.access_token}"
        }
        params = {"login": BROADCASTER_USER_LOGIN}
        
        async with self.session.get(url, headers=headers, params=params) as response:
            if response.status == 200:
                data = await response.json()
                if data["data"]:
                    self.broadcaster_user_id = data["data"][0]["id"]
                    logger.info(f"ID du broadcaster obtenu: {self.broadcaster_user_id}")
                    return True
                else:
                    logger.error("Broadcaster non trouvé")
                    return False
            else:
                logger.error(f"Erreur lors de la récupération de l'ID: {response.status}")
                return False
    
    async def create_eventsub_subscription(self):
        """Crée un abonnement EventSub pour les channel point redemptions"""
        url = "https://api.twitch.tv/helix/eventsub/subscriptions"
        headers = {
            "Client-Id": TWITCH_CLIENT_ID,
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "type": "channel.channel_points_custom_reward_redemption.add",
            "version": "1",
            "condition": {
                "broadcaster_user_id": self.broadcaster_user_id
            },
            "transport": {
                "method": "webhook",
                "callback": CALLBACK_URL,
                "secret": WEBHOOK_SECRET
            }
        }
        
        async with self.session.post(url, headers=headers, json=payload) as response:
            if response.status == 202:
                data = await response.json()
                logger.info("Abonnement EventSub créé avec succès")
                logger.info(f"ID de l'abonnement: {data['data'][0]['id']}")
                return True
            else:
                error_data = await response.json()
                logger.error(f"Erreur lors de la création de l'abonnement: {response.status}")
                logger.error(f"Détails: {error_data}")
                return False
    
    async def delete_all_subscriptions(self):
        """Supprime TOUS les abonnements EventSub existants (pas juste les channel points)"""
        url = "https://api.twitch.tv/helix/eventsub/subscriptions"
        headers = {
            "Client-Id": TWITCH_CLIENT_ID,
            "Authorization": f"Bearer {self.access_token}"
        }
        
        # Récupérer tous les abonnements
        async with self.session.get(url, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                logger.info(f"Trouvé {len(data['data'])} abonnements à supprimer")
                
                # Supprimer chaque abonnement
                for sub in data['data']:
                    delete_url = f"{url}?id={sub['id']}"
                    async with self.session.delete(delete_url, headers=headers) as del_response:
                        if del_response.status == 204:
                            logger.info(f"Abonnement supprimé: {sub['id']} (Type: {sub['type']}, Status: {sub['status']})")
                        else:
                            logger.error(f"Erreur suppression {sub['id']}: {del_response.status}")
            else:
                logger.error(f"Erreur récupération abonnements: {response.status}")
    
    async def list_subscriptions(self):
        """Liste tous les abonnements EventSub actifs"""
        url = "https://api.twitch.tv/helix/eventsub/subscriptions"
        headers = {
            "Client-Id": TWITCH_CLIENT_ID,
            "Authorization": f"Bearer {self.access_token}"
        }
        
        async with self.session.get(url, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                logger.info("Abonnements actifs:")
                for sub in data["data"]:
                    logger.info(f"  - {sub['type']} (ID: {sub['id']}, Status: {sub['status']})")
                return data["data"]
            else:
                logger.error(f"Erreur lors de la récupération des abonnements: {response.status}")
                return []

def verify_signature(message_signature, message_timestamp, message_body, secret):
    """Vérifie la signature du webhook Twitch"""
    try:
        # Assurer que tout est en string
        timestamp_str = str(message_timestamp)
        body_str = str(message_body)
        secret_str = str(secret)
        
        # Créer le message à signer
        message = timestamp_str + body_str
        
        # Calculer la signature attendue
        expected_signature = hmac.new(
            secret_str.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        expected_signature_formatted = f"sha256={expected_signature}"
        
        # Debug
        logger.info(f"DEBUG - Expected signature: {expected_signature_formatted}")
        logger.info(f"DEBUG - Received signature: {message_signature}")
        
        # Comparer
        result = hmac.compare_digest(expected_signature_formatted, str(message_signature))
        logger.info(f"DEBUG - Signatures match: {result}")
        
        return result
        
    except Exception as e:
        logger.error(f"Erreur dans verify_signature: {e}")
        return False

async def handle_webhook(request):
    """Gestionnaire pour les webhooks Twitch"""
    message_signature = request.headers.get('Twitch-Eventsub-Message-Signature')
    message_timestamp = request.headers.get('Twitch-Eventsub-Message-Timestamp')
    message_type = request.headers.get('Twitch-Eventsub-Message-Type')
    
    body = await request.text()
    
    # Debug des headers et signature
    logger.info(f"DEBUG - Signature reçue: {message_signature}")
    logger.info(f"DEBUG - Timestamp: {message_timestamp}")
    logger.info(f"DEBUG - Message type: {message_type}")
    logger.info(f"DEBUG - Secret utilisé: {WEBHOOK_SECRET[:10]}...")
    
    # Vérification de la signature
    if not verify_signature(message_signature, message_timestamp, body, WEBHOOK_SECRET):
        logger.warning("Signature webhook invalide")
        logger.info(f"DEBUG - Body reçu: {body[:200]}...")
        return web.Response(status=403)
    
    # Vérification du timestamp (évite les attaques de replay)
    current_time = int(time.time())
    message_time = int(message_timestamp)
    if abs(current_time - message_time) > 600:  # 10 minutes
        logger.warning("Message webhook trop ancien")
        return web.Response(status=403)
    
    data = json.loads(body)
    
    # Gestion des différents types de messages
    if message_type == 'webhook_callback_verification':
        # Confirmation de l'abonnement
        logger.info("Confirmation de l'abonnement webhook")
        return web.Response(text=data['challenge'])
    
    elif message_type == 'notification':
        # Événement de dépense de points de chaîne
        event = data['event']
        logger.info("=== CHANNEL POINTS REDEMPTION ===")
        logger.info(f"Utilisateur: {event['user_name']} (ID: {event['user_id']})")
        logger.info(f"Récompense: {event['reward']['title']}")
        logger.info(f"Coût: {event['reward']['cost']} points")
        logger.info(f"Message utilisateur: {event.get('user_input', 'Aucun message')}")
        logger.info(f"Statut: {event['status']}")
        logger.info(f"Timestamp: {event['redeemed_at']}")
        
        # Ici vous pouvez ajouter votre logique personnalisée
        await process_channel_points_redemption(event)
        
        return web.Response(status=204)
    
    elif message_type == 'revocation':
        # Révocation de l'abonnement
        logger.info("Abonnement révoqué")
        return web.Response(status=204)
    
    return web.Response(status=200)

async def process_channel_points_redemption(event):
    """Traite les événements de dépense de points de chaîne"""
    user_name = event['user_name']
    reward_title = event['reward']['title']
    cost = event['reward']['cost']
    user_input = event.get('user_input', '')
    
    # Exemple de logiques personnalisées basées sur le type de récompense
    if "song" in reward_title.lower() or "musique" in reward_title.lower():
        logger.info(f"🎵 Demande de musique de {user_name}: {user_input}")
        # Ajouter à une playlist, etc.
    
    elif "message" in reward_title.lower() or "tts" in reward_title.lower():
        logger.info(f"💬 Message TTS de {user_name}: {user_input}")
        # Envoyer au TTS, etc.
    
    elif "jeu" in reward_title.lower() or "game" in reward_title.lower():
        logger.info(f"🎮 Action de jeu demandée par {user_name}")
        # Effectuer une action dans le jeu, etc.
    
    else:
        logger.info(f"✨ Récompense personnalisée '{reward_title}' utilisée par {user_name}")
    
    # Vous pouvez aussi sauvegarder dans une base de données
    # save_to_database(event)

async def health_check(request):
    """Route de test pour vérifier que le serveur fonctionne"""
    return web.Response(text="Bot Twitch Channel Points - Serveur actif !", status=200)

async def start_webhook_server():
    """Démarre le serveur webhook"""
    app = web.Application()
    app.router.add_post('/webhook', handle_webhook)
    app.router.add_get('/', health_check)  # Route pour éviter les 404
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Utilise le port fourni par Render (ou 8080 en local)
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    logger.info(f"Serveur webhook démarré sur port {port}")
    logger.info("Prêt à recevoir les webhooks Twitch !")

async def main():
    """Fonction principale"""
    eventsub = TwitchEventSub()
    
    # Initialisation de la session HTTP
    eventsub.session = ClientSession()
    
    try:
        # 1. Obtenir le token d'accès
        if not await eventsub.get_app_access_token():
            return
        
        # 2. Obtenir l'ID du broadcaster
        if not await eventsub.get_broadcaster_id():
            return
        
        # 3. Nettoyer les anciens abonnements (debug)
        logger.info("Nettoyage des anciens abonnements...")
        await eventsub.delete_all_subscriptions()
        
        # 4. Lister les abonnements existants (optionnel)
        await eventsub.list_subscriptions()
        
        # 5. Créer l'abonnement EventSub
        if not await eventsub.create_eventsub_subscription():
            return
        
        # 6. Démarrer le serveur webhook
        await start_webhook_server()
        
        logger.info("Script en cours d'exécution. Appuyez sur Ctrl+C pour arrêter.")
        
        # Garder le script en vie
        while True:
            await asyncio.sleep(1)
    
    except KeyboardInterrupt:
        logger.info("Arrêt du script...")
    
    finally:
        # Nettoyage
        if eventsub.session:
            await eventsub.session.close()

if __name__ == "__main__":
    # Vérification de la configuration
    if not TWITCH_CLIENT_ID or TWITCH_CLIENT_ID == "votre_client_id_ici":
        logger.error("❌ TWITCH_CLIENT_ID manquant dans les variables d'environnement")
        exit(1)
    
    if not TWITCH_CLIENT_SECRET or TWITCH_CLIENT_SECRET == "votre_client_secret_ici":
        logger.error("❌ TWITCH_CLIENT_SECRET manquant dans les variables d'environnement")
        exit(1)
    
    if not WEBHOOK_SECRET or WEBHOOK_SECRET == "votre_secret_webhook_ici":
        logger.error("❌ WEBHOOK_SECRET manquant dans les variables d'environnement")
        exit(1)
    
    if not CALLBACK_URL or CALLBACK_URL == "https://votre-app.onrender.com/webhook":
        logger.error("❌ CALLBACK_URL manquant dans les variables d'environnement")
        exit(1)
    
    if not BROADCASTER_USER_LOGIN or BROADCASTER_USER_LOGIN == "nom_du_streamer":
        logger.error("❌ BROADCASTER_USER_LOGIN manquant dans les variables d'environnement")
        exit(1)
    
    logger.info("✅ Configuration OK, démarrage du script...")
    logger.info(f"🔍 Debug - WEBHOOK_SECRET: '{WEBHOOK_SECRET}'")
    logger.info(f"🔍 Debug - Longueur du secret: {len(WEBHOOK_SECRET)} caractères")
    logger.info(f"🔍 Debug - CALLBACK_URL: {CALLBACK_URL}")
    logger.info(f"🔍 Debug - BROADCASTER: {BROADCASTER_USER_LOGIN}")
    
    # Lancement du script
    asyncio.run(main())
