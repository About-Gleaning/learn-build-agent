class TodoManager:
    def __init__(self):
        self.todos = []
    
    def update(self, todo_list):
        """Update the todo list with validation."""
        # Validate list length
        if len(todo_list) > 20:
            raise ValueError("Todo list cannot exceed 20 items")
        
        # Process and validate each todo item
        processed_todos = []
        in_progress_count = 0
        
        for i, todo in enumerate(todo_list):
            # Handle id
            if todo.get('id') is None or todo['id'] == '':
                todo_id = i + 1
            else:
                todo_id = todo['id']
            
            # Handle text
            text = todo.get('text', '').strip()
            if not text:
                raise ValueError(f"Todo item {i+1} has empty text after stripping whitespace")
            
            # Handle status
            status = todo.get('status', '').lower().strip()
            valid_statuses = ['pending', 'in_progress', 'completed']
            if status not in valid_statuses:
                raise ValueError(f"Invalid status '{status}' for todo item {i+1}. Must be one of: {valid_statuses}")
            
            if status == 'in_progress':
                in_progress_count += 1
                if in_progress_count > 1:
                    raise ValueError("Only one todo can be in progress at a time")
            
            processed_todos.append({
                'id': todo_id,
                'text': text,
                'status': status
            })
        
        self.todos = processed_todos
        return self.render()
    
    def render(self):
        """Render todos as formatted string."""
        if not self.todos:
            return "No todos."
        
        lines = []
        completed_count = 0
        
        for todo in self.todos:
            if todo['status'] == 'completed':
                status_char = 'x'
                completed_count += 1
            elif todo['status'] == 'in_progress':
                status_char = '>'
            else:  # pending
                status_char = ' '
            
            lines.append(f"[{status_char}] #{todo['id']}: {todo['text']}")
        
        total_count = len(self.todos)
        completion_info = f"({completed_count}/{total_count} completed)"
        lines.append("")
        lines.append(completion_info)
        
        return "\n".join(lines)