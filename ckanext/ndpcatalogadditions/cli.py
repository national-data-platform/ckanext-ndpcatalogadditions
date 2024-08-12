import click


@click.group(short_help="ndpcatalogadditions CLI.")
def ndpcatalogadditions():
    """ndpcatalogadditions CLI.
    """
    pass


@ndpcatalogadditions.command()
@click.argument("name", default="ndpcatalogadditions")
def command(name):
    """Docs.
    """
    click.echo("Hello, {name}!".format(name=name))


def get_commands():
    return [ndpcatalogadditions]
