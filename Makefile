.PHONY: help install create-binary clean clean-build clean-cache run test-binary install-binary uninstall-binary

# Default target
help:
	@echo "TimeBuddy Makefile Commands:"
	@echo ""
	@echo "  make install          - Install dependencies"
	@echo "  make create-binary    - Create standalone executable binary"
	@echo "  make clean            - Remove all build artifacts and cache"
	@echo "  make clean-build      - Remove build artifacts only"
	@echo "  make clean-cache      - Clear application cache"
	@echo "  make run              - Run the application (last 7 days)"
	@echo "  make install-binary   - Install binary to /usr/local/bin"
	@echo "  make uninstall-binary - Remove binary from /usr/local/bin"
	@echo "  make test-binary      - Test the binary"
	@echo ""

# Install dependencies
install:
	pip install -r requirements.txt
	pip install pyinstaller

# Create standalone executable binary
create-binary: clean-build
	@echo "Building standalone executable..."
	pyinstaller --onefile --name time-buddy time_buddy.py
	@echo ""
	@echo "✅ Binary created successfully!"
	@echo "📦 Location: dist/time-buddy"
	@echo "📊 Size: $$(du -h dist/time-buddy | cut -f1)"
	@echo ""
	@echo "To install globally, run: make install-binary"

# Clean all artifacts
clean: clean-build clean-cache
	@echo "✅ All cleaned!"

# Clean build artifacts
clean-build:
	@echo "Cleaning build artifacts..."
	rm -rf build/ dist/ *.spec __pycache__/ *.egg-info/
	@echo "✅ Build artifacts removed"

# Clear application cache
clean-cache:
	@echo "Clearing application cache..."
	@if [ -d "$$HOME/Library/Application Support/TimeBuddy" ]; then \
		rm -rf "$$HOME/Library/Application Support/TimeBuddy"; \
		echo "✅ Application cache cleared"; \
	else \
		echo "ℹ️  No cache to clear"; \
	fi

# Run the application
run:
	python time_buddy.py --days 7

# Test the binary
test-binary: create-binary
	@echo "Testing binary..."
	./dist/time-buddy --help
	@echo ""
	@echo "✅ Binary test passed!"

# Install binary to system path
install-binary: create-binary
	@if [ ! -f dist/time-buddy ]; then \
		echo "❌ Binary not found. Run 'make create-binary' first."; \
		exit 1; \
	fi
	@echo "Installing binary to /usr/local/bin/..."
	sudo cp dist/time-buddy /usr/local/bin/time-buddy
	sudo chmod +x /usr/local/bin/time-buddy
	@echo "✅ Binary installed successfully!"
	@echo "You can now run: time-buddy --days 7"

# Uninstall binary from system path
uninstall-binary:
	@if [ -f /usr/local/bin/time-buddy ]; then \
		echo "Removing binary from /usr/local/bin/..."; \
		sudo rm /usr/local/bin/time-buddy; \
		echo "✅ Binary uninstalled successfully!"; \
	else \
		echo "ℹ️  Binary not installed"; \
	fi
