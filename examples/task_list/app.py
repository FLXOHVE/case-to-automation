"""A fictional, in-memory task list used only by the offline demonstration."""


class TaskList:
    def __init__(self):
        self.tasks = {"A": {"completed": False, "archived": False}}

    def complete(self, task_id):
        self.tasks[task_id]["completed"] = True

    def archive(self, task_id):
        task = self.tasks[task_id]
        if not task["completed"]:
            raise ValueError("Only completed tasks can be archived")
        task["archived"] = True

    def archived_ids(self):
        return [key for key, task in self.tasks.items() if task["archived"]]
