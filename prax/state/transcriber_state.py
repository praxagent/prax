class ChatBot:
    def __init__(self):
        self.state = 'INITIAL'

    def process_message(self, message):
        if self.state == 'INITIAL':
            if self.check_keyword(message, ['help', 'assist']):
                response = self.handle_help_request()
            elif self.check_keyword(message, ['next', 'step']):
                response = self.handle_next_state()
                self.state = 'NEXT_STATE'
            elif self.check_keyword(message, ['menu']):
                response = self.show_initial_menu()
            else:
                response = self.handle_default_state()
        elif self.state == 'NEXT_STATE':
            if self.check_keyword(message, ['back', 'previous']):
                response = self.handle_previous_state()
                self.state = 'PREVIOUS_STATE'
            elif self.check_keyword(message, ['another', 'next']):
                response = self.handle_another_state()
                self.state = 'ANOTHER_STATE'
            elif self.check_keyword(message, ['menu']):
                response = self.show_next_state_menu()
            else:
                response = self.handle_default_state()
        elif self.state == 'ANOTHER_STATE':
            if self.check_keyword(message, ['end', 'finish']):
                response = self.handle_end_state()
                self.state = 'END'
            elif self.check_keyword(message, ['menu']):
                response = self.show_another_state_menu()
            else:
                response = self.handle_default_state()
        elif self.state == 'END':
            response = self.handle_end_state()
        else:
            response = "Unknown state. Please start again."

        return response

    def check_keyword(self, message, keywords):
        message = message.lower()
        for keyword in keywords:
            if keyword in message:
                return True
        return False

    def handle_help_request(self):
        return "How can I assist you?"

    def handle_next_state(self):
        return "Here is the next step."

    def handle_previous_state(self):
        return "Going back to the previous state."

    def handle_another_state(self):
        return "What would you like to do next?"

    def handle_default_state(self):
        return "I'm sorry, I didn't understand that."

    def handle_end_state(self):
        return "Thank you for using the chatbot. Goodbye!"

    def show_initial_menu(self):
        menu = "Available commands:\n" \
               "- help\n" \
               "- next step\n" \
               "- menu"
        return f"Here are the available commands:\n{menu}"

    def show_next_state_menu(self):
        menu = "Available commands:\n" \
               "- back\n" \
               "- another next\n" \
               "- menu"
        return f"Here are the available commands:\n{menu}"

    def show_another_state_menu(self):
        menu = "Available commands:\n" \
               "- end\n" \
               "- menu"
        return f"Here are the available commands:\n{menu}"


chatbot = ChatBot()
while True:
    user_input = input("User: ")
    response = chatbot.process_message(user_input)
    print("ChatBot:", response)
