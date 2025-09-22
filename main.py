import asyncio
import json
import os
from datetime import datetime
import aiohttp
import logging
import websockets

# Configuration via variables d'environnement
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID", "votre_client_id_ici")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET", "votre_client_secret_ici")
BROADCASTER_USER_LOGIN = os.getenv("BROADCASTER_USER_LOGIN", "nom_du_streamer")

# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TwitchEventSubWebSocket:
    def __init__(self):
        self.access_token = None
        self.broadcaster_user_id = None
        self.session = None
        self.websocket = None
        self.session_id = None
        
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
    
    async def connect_websocket(self):
        """Se connecte au WebSocket EventSub de Twitch"""
        websocket_url = "wss://eventsub.wss.twitch.tv/ws"
        
        try:
            self.websocket = await websockets.connect(websocket_url)
            logger.info("Connexion WebSocket établie")
            
            # Écouter le message de bienvenue
            welcome_message = await self.websocket.recv()
            welcome_data = json.loads(welcome_message)
            
            if welcome_data["metadata"]["message_type"] == "session_welcome":
                self.session_id = welcome_data["payload"]["session"]["id"]
                logger.info(f"Session WebSocket établie: {self.session_id}")
                return True
            else:
                logger.error("Message de bienvenue attendu non reçu")
                return False
                
        except Exception as e:
            logger.error(f"Erreur de connexion WebSocket: {e}")
            return False
    
    async def create_eventsub_subscription(self):
        """Crée un abonnement EventSub pour les channel point redemptions via WebSocket"""
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
                "method": "websocket",
                "session_id": self.session_id
            }
        }
        
        async with self.session.post(url, headers=headers, json=payload) as response:
            if response.status == 202:
                data = await response.json()
                logger.info("Abonnement EventSub WebSocket créé avec succès")
                logger.info(f"ID de l'abonnement: {data['data'][0]['id']}")
                return True
            else:
                error_data = await response.json()
                logger.error(f"Erreur lors de la création de l'abonnement: {response.status}")
                logger.error(f"Détails: {error_data}")
                return False
    
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
    
    async def listen_for_events(self):
        """Écoute les événements sur le WebSocket"""
        logger.info("Écoute des événements channel points en cours...")
        
        try:
            while True:
                message = await self.websocket.recv()
                data = json.loads(message)
                
                message_type = data["metadata"]["message_type"]
                
                if message_type == "notification":
                    event = data["payload"]["event"]
                    await self.process_channel_points_redemption(event)
                    
                elif message_type == "session_keepalive":
                    logger.debug("Keepalive reçu")
                    
                elif message_type == "session_reconnect":
                    logger.info("Reconnexion demandée par Twitch")
                    # Ici on pourrait implémenter la reconnexion automatique
                    break
                    
                else:
                    logger.info(f"Message reçu: {message_type}")
                    
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Connexion WebSocket fermée")
        except Exception as e:
            logger.error(f"Erreur lors de l'écoute des événements: {e}")

async def process_channel_points_redemption(event):
    """Traite les événements de dépense de points de chaîne"""
    user_name = event['user_name']
    reward_title = event['reward']['title']
    cost = event['reward']['cost']
    user_input = event.get('user_input', '')
    
    logger.info("=== CHANNEL POINTS REDEMPTION ===")
    logger.info(f"Utilisateur: {user_name} (ID: {event['user_id']})")
    logger.info(f"Récompense: {reward_title}")
    logger.info(f"Coût: {cost} points")
    logger.info(f"Message utilisateur: {user_input if user_input else 'Aucun message'}")
    logger.info(f"Statut: {event['status']}")
    logger.info(f"Timestamp: {event['redeemed_at']}")
    
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
    return aiohttp.web.Response(text="Bot Twitch Channel Points WebSocket - Serveur actif !", status=200)

async def start_health_server():
    """Démarre un serveur de santé simple pour Render"""
    app = aiohttp.web.Application()
    app.router.add_get('/', health_check)
    
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 8080))
    site = aiohttp.web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    logger.info(f"Serveur de santé démarré sur port {port}")

async def main():
    """Fonction principale"""
    eventsub = TwitchEventSubWebSocket()
    
    # Initialisation de la session HTTP
    eventsub.session = aiohttp.ClientSession()
    
    try:
        # 1. Démarrer le serveur de santé (pour Render)
        await start_health_server()
        
        # 2. Obtenir le token d'accès
        if not await eventsub.get_app_access_token():
            return
        
        # 3. Obtenir l'ID du broadcaster
        if not await eventsub.get_broadcaster_id():
            return
        
        # 4. Se connecter au WebSocket
        if not await eventsub.connect_websocket():
            return
        
        # 5. Créer l'abonnement EventSub
        if not await eventsub.create_eventsub_subscription():
            return
        
        # 6. Lister les abonnements (optionnel)
        await eventsub.list_subscriptions()
        
        logger.info("Bot prêt ! En attente des channel points...")
        
        # 7. Écouter les événements
        await eventsub.listen_for_events()
    
    except KeyboardInterrupt:
        logger.info("Arrêt du script...")
    
    finally:
        # Nettoyage
        if eventsub.websocket:
            await eventsub.websocket.close()
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
    
    if not BROADCASTER_USER_LOGIN or BROADCASTER_USER_LOGIN == "nom_du_streamer":
        logger.error("❌ BROADCASTER_USER_LOGIN manquant dans les variables d'environnement")
        exit(1)
    
    logger.info("✅ Configuration OK, démarrage du bot WebSocket...")
    
    # Lancement du script
    asyncio.run(main())
