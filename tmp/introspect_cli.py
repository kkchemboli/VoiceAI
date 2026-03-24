from livekit.agents import cli
import click

def print_commands(group, prefix=""):
    for name, command in group.commands.items():
        print(f"{prefix}{name}")
        if isinstance(command, click.Group):
            print_commands(command, prefix + "  ")

if __name__ == "__main__":
    # Create the app group as cli.run_app would
    app = cli.app
    print("Available subcommands:")
    print_commands(app)
