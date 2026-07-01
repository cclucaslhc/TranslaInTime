import argparse
import sys

import argostranslate.package


def main() -> int:
    parser = argparse.ArgumentParser(description="Install an Argos Translate offline language package.")
    parser.add_argument("--from", dest="source", default="en", help="Source language code, e.g. en")
    parser.add_argument("--to", dest="target", default="zh", help="Target language code, e.g. zh")
    args = parser.parse_args()

    print(f"Updating Argos package index for {args.source}->{args.target}...")
    argostranslate.package.update_package_index()
    packages = argostranslate.package.get_available_packages()
    package = next(
        (pkg for pkg in packages if pkg.from_code == args.source and pkg.to_code == args.target),
        None,
    )
    if package is None:
        print(f"No Argos package found for {args.source}->{args.target}.", file=sys.stderr)
        return 1

    print(f"Downloading {package}...")
    path = package.download()
    print(f"Installing {path}...")
    argostranslate.package.install_from_path(path)
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
