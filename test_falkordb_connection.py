from falkordb import FalkorDB


def main():
    db = FalkorDB(host="localhost", port=6379)

    try:
        # Ask FalkorDB for the list of existing graphs.
        graphs = db.list_graphs()

        print("✅ Connected to FalkorDB successfully!")
        print(f"Existing graphs: {graphs}")

    except Exception as error:
        print("❌ Could not connect to FalkorDB.")
        print(f"Error: {error}")
        raise

    finally:
        db.close()


if __name__ == "__main__":
    main()