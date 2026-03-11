#!/bin/bash

# Telegram Media Downloader - Linux Startup Script
# This script handles installation, configuration check, and startup

set -e  # Exit on any error

# Parse command line arguments
SKIP_INSTALL=false
SKIP_CONFIG=false
SHOW_LOGS=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-install)
            SKIP_INSTALL=true
            shift
            ;;
        --skip-config)
            SKIP_CONFIG=true
            shift
            ;;
        --show-logs)
            SHOW_LOGS=true
            shift
            ;;
        --help|-h)
            echo "Telegram Media Downloader - Linux Startup Script"
            echo "==============================================="
            echo ""
            echo "Usage: ./start.sh [options]"
            echo ""
            echo "Options:"
            echo "  --skip-install    Skip dependency installation"
            echo "  --skip-config     Skip configuration validation"
            echo "  --show-logs       Show application logs in real-time"
            echo "  --help, -h        Show this help message"
            echo ""
            echo "This script will:"
            echo "  1. Check Node.js and npm installation"
            echo "  2. Install dependencies (unless --skip-install)"
            echo "  3. Validate configuration (unless --skip-config)"
            echo "  4. Start the application"
            echo "  5. Show logs in real-time (if --show-logs is specified)"
            echo ""
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

echo "🤖 Telegram Media Downloader - Linux Startup Script"
echo "=================================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to check Node.js version
check_nodejs() {
    if ! command_exists node; then
        print_error "Node.js is not installed. Please install Node.js 18+ first."
        print_error "Visit: https://nodejs.org/"
        exit 1
    fi

    local node_version=$(node --version | sed 's/v//')
    local major_version=$(echo $node_version | cut -d. -f1)

    if [ "$major_version" -lt 18 ]; then
        print_error "Node.js version $node_version is too old. Please upgrade to Node.js 18+"
        exit 1
    fi

    print_success "Node.js version: $node_version"
}

# Function to check npm
check_npm() {
    if ! command_exists npm; then
        print_error "npm is not installed. Please install npm first."
        exit 1
    fi

    local npm_version=$(npm --version)
    print_success "npm version: $npm_version"
}

# Function to install dependencies
install_dependencies() {
    if [ "$SKIP_INSTALL" = true ]; then
        print_status "Skipping dependency installation (--skip-install)"
        return
    fi

    print_status "Installing dependencies..."

    if [ ! -f "package.json" ]; then
        print_error "package.json not found. Are you in the correct directory?"
        exit 1
    fi

    npm install

    if [ $? -eq 0 ]; then
        print_success "Dependencies installed successfully"
    else
        print_error "Failed to install dependencies"
        exit 1
    fi
}

# Function to check configuration
check_config() {
    if [ "$SKIP_CONFIG" = true ]; then
        print_status "Skipping configuration check (--skip-config)"
        return
    fi

    print_status "Checking configuration..."

    local config_complete=true

    # Check .env file
    if [ -f ".env" ]; then
        print_success ".env file exists"

        # Check required environment variables
        local required_vars=("BOT_TOKEN" "BOT_API_HOST" "PUBLIC_FILE_BASE_URL")

        for var in "${required_vars[@]}"; do
            if ! grep -q "^${var}=" .env; then
                print_warning "Environment variable ${var} not found in .env"
                config_complete=false
            fi
        done

        # Check optional user API variables
        if grep -q "^USER_API_ID=" .env && grep -q "^USER_API_HASH=" .env; then
            print_success "User API configuration found (channel features enabled)"
        else
            print_warning "User API not configured (channel features disabled)"
        fi
    else
        print_warning ".env file not found. Checking config.yaml..."

        if [ ! -f "config.yaml" ]; then
            print_error "Neither .env nor config.yaml found. Please create configuration file."
            config_complete=false
        else
            print_success "config.yaml file exists"
            # Basic check for bot_token
            if ! grep -q "bot_token:" config.yaml; then
                print_warning "bot_token not found in config.yaml"
                config_complete=false
            fi
        fi
    fi

    if [ "$config_complete" = true ]; then
        print_success "Configuration check passed"
    else
        print_warning "Configuration incomplete. Please check your settings."
        echo ""
        print_status "Required settings:"
        echo "  - BOT_TOKEN (from @BotFather)"
        echo "  - BOT_API_HOST (your Bot API server)"
        echo "  - PUBLIC_FILE_BASE_URL (public file access URL)"
        echo ""
        print_status "Optional (for channel features):"
        echo "  - USER_API_ID and USER_API_HASH (from https://my.telegram.org)"
        echo ""
        read -p "Continue anyway? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
}

# Function to start the application
start_app() {
    print_status "Starting Telegram Media Downloader..."

    # Check if already running
    if pgrep -f "node src/index.js" > /dev/null; then
        print_warning "Application appears to be already running"
        read -p "Kill existing process and start new one? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            pkill -f "node src/index.js"
            sleep 2
        else
            print_status "Exiting..."
            exit 0
        fi
    fi

    # Start the application
    if [ "$SHOW_LOGS" = true ]; then
        print_status "Starting application in foreground (showing logs)..."
        print_status "Press Ctrl+C to stop the application"
        echo ""
        npm start
    else
        npm start &
        local pid=$!

        print_success "Application started with PID: $pid"
        print_status "Use 'kill $pid' to stop the application"
        print_status "Check logs with 'tail -f combined.log'"
        print_status "Or run with --show-logs to see logs in real-time"

        # Wait a bit and check if it's still running
        sleep 3
        if kill -0 $pid 2>/dev/null; then
            print_success "Application is running successfully"
        else
            print_error "Application failed to start. Check logs for details."
            exit 1
        fi
    fi
}

# Main script
main() {
    echo ""

    # Check prerequisites
    check_nodejs
    check_npm

    echo ""

    # Install dependencies
    install_dependencies

    echo ""

    # Check configuration
    check_config

    echo ""

    # Start application
    start_app

    echo ""
    if [ "$SHOW_LOGS" = true ]; then
        print_success "🎉 Application is running with logs displayed above."
        print_status "Press Ctrl+C to stop the application."
    else
        print_success "🎉 Setup complete! Telegram Media Downloader is now running."
        print_status "You can now send media files to your bot or use the commands."
        echo ""
        read -p "Press Enter to exit: "
    fi
}

# Run main function
main "$@"