import logging

from flask import Blueprint, Response, current_app, make_response, request, send_from_directory, session

from prax.blueprints.twilio_auth import validate_twilio_request
from prax.services.voice_service import VoiceAccessError, voice_service

logger = logging.getLogger(__name__)

main_routes = Blueprint('main_routes', __name__)

@main_routes.route('/respond', methods=['POST'])
@validate_twilio_request
def respond():
    ngrok_url = current_app.config['NGROK_URL']
    call_sid = request.values.get('CallSid', '')
    from_number = request.values.get('From', '')
    voice_input = request.form.get('SpeechResult', '')

    resp = voice_service.handle_response(call_sid, from_number, voice_input, ngrok_url)
    flask_response = make_response(resp.to_xml())
    flask_response.headers['Content-Type'] = 'application/xml'
    return flask_response



@main_routes.route('/health')
def health():
    """Health check endpoint for the watchdog supervisor."""
    return 'ok', 200


@main_routes.route('/static/<path:path>')
def serve_static(path):
    return send_from_directory('static', path)


@main_routes.route('/shared/<token>/<filename>')
def serve_shared_file(token, filename):
    """Serve a file that Prax has explicitly published via a share token.

    Only files published with workspace_service.publish_file() are accessible.
    The token is a random UUID — files cannot be guessed or enumerated.
    """
    import os

    from prax.services.workspace_service import get_published_file
    file_path = get_published_file(token, filename=filename)
    if not file_path or not os.path.isfile(file_path):
        return "Not found", 404
    directory = os.path.dirname(file_path)
    return send_from_directory(directory, os.path.basename(file_path))

@main_routes.route('/courses/')
@main_routes.route('/courses/<path:path>')
def serve_course_site(path=''):
    """Serve Hugo-generated static course pages.

    Course pages are public (shared via ngrok links) so no auth is needed.
    We scan all user workspaces to find which one has a built Hugo site
    containing the requested path.
    """
    import os

    from prax.services.course_service import find_course_site_public_dir

    public_dir = find_course_site_public_dir(path)
    if not public_dir:
        return "Page not found.", 404

    # Serve index.html for directory paths.
    file_path = os.path.join(public_dir, path)
    if os.path.isdir(file_path):
        file_path = os.path.join(file_path, "index.html")
        path = os.path.join(path, "index.html")

    if not os.path.isfile(file_path):
        return "Page not found.", 404

    directory = os.path.dirname(file_path)
    return send_from_directory(directory, os.path.basename(file_path))


@main_routes.route('/notes/')
@main_routes.route('/notes/<path:path>')
def serve_notes(path=''):
    """Serve notes — redirects into the Hugo site's notes section."""
    return serve_course_site(f"notes/{path}" if path else "notes/")


@main_routes.route('/news/')
@main_routes.route('/news/<path:path>')
def serve_news(path=''):
    """Serve news briefings — redirects into the Hugo site's news section."""
    return serve_course_site(f"news/{path}" if path else "news/")


@main_routes.route('/transcribe', methods=['POST'])
@validate_twilio_request
def transcribe():
    from_num = request.form.get('From', "")
    call_sid = request.form.get('CallSid', "")

    try:
        resp = voice_service.handle_transcribe(call_sid, from_num, session)
        return Response(str(resp), mimetype='application/xml')
    except VoiceAccessError:
        return 'Not found', 404
    except Exception as exc:  # pragma: no cover - logged for operators
        logger.error("Error in transcribe: %s", exc)
        return '', 500


@main_routes.errorhandler(400)
def bad_request_error(error):
    return f"Bad Request: {error.description}", 400
