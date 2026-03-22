import logging

from flask import Blueprint, current_app, request

from prax.blueprints.twilio_auth import validate_twilio_request
from prax.services.sms_service import SmsAccessError, sms_service

logger = logging.getLogger(__name__)

textchat_routes = Blueprint('textchat_routes', __name__)


@textchat_routes.route("/sms", methods=['POST'])
@validate_twilio_request
def sms_reply():
    try:
        body, status = sms_service.process(request.values, current_app.config['NGROK_URL'])
        return body, status
    except SmsAccessError:
        return 'Not found', 404
    except Exception as exc:  # pragma: no cover
        logger.error("SMS handler error: %s", exc)
        return '', 500
