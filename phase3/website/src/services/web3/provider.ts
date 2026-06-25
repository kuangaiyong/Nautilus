import { ethers } from 'ethers';
import type { NetworkConfig } from './types';

// Network configurations
export const NETWORKS: Record<string, NetworkConfig> = {
  // 内网私有链（geth Clique POA）。托管钱包模式下前端主要通过后端 API 操作，
  // 此配置用于可选的 MetaMask 直连私链场景。
  privatechain: {
    chainId: 13370,
    chainName: 'Nautilus 私有链',
    nativeCurrency: {
      name: 'Ether',
      symbol: 'ETH',
      decimals: 18,
    },
    rpcUrls: [
      (import.meta.env.VITE_PRIVATE_RPC as string) || 'http://127.0.0.1:8545',
    ],
    blockExplorerUrls: [],
  },
  sepolia: {
    chainId: 11155111,
    chainName: 'Sepolia Testnet',
    nativeCurrency: {
      name: 'Sepolia ETH',
      symbol: 'ETH',
      decimals: 18,
    },
    rpcUrls: [
      'https://sepolia.infura.io/v3/YOUR_INFURA_KEY',
      'https://rpc.sepolia.org',
      'https://ethereum-sepolia.publicnode.com',
    ],
    blockExplorerUrls: ['https://sepolia.etherscan.io'],
  },
  mainnet: {
    chainId: 1,
    chainName: 'Ethereum Mainnet',
    nativeCurrency: {
      name: 'Ether',
      symbol: 'ETH',
      decimals: 18,
    },
    rpcUrls: [
      'https://mainnet.infura.io/v3/YOUR_INFURA_KEY',
      'https://eth.llamarpc.com',
    ],
    blockExplorerUrls: ['https://etherscan.io'],
  },
};

// Default network（内网部署默认私有链，可经 VITE_NETWORK 覆盖）
export const DEFAULT_NETWORK = (import.meta.env.VITE_NETWORK as string) || 'privatechain';

class Web3Provider {
  private provider: ethers.BrowserProvider | null = null;
  private signer: ethers.Signer | null = null;
  private currentNetwork: string = DEFAULT_NETWORK;

  /**
   * Initialize provider with MetaMask or other injected wallet
   */
  async initializeProvider(): Promise<ethers.BrowserProvider> {
    if (typeof window === 'undefined' || !window.ethereum) {
      throw new Error('No Web3 provider found. Please install MetaMask.');
    }

    try {
      this.provider = new ethers.BrowserProvider(window.ethereum);
      return this.provider;
    } catch (error) {
      throw new Error(`Failed to initialize provider: ${(error as Error).message}`);
    }
  }

  /**
   * Get current provider instance
   */
  getProvider(): ethers.BrowserProvider {
    if (!this.provider) {
      throw new Error('Provider not initialized. Call initializeProvider() first.');
    }
    return this.provider;
  }

  /**
   * Get signer for transactions
   */
  async getSigner(): Promise<ethers.Signer> {
    const provider = this.getProvider();

    try {
      this.signer = await provider.getSigner();
      return this.signer;
    } catch (error) {
      throw new Error(`Failed to get signer: ${(error as Error).message}`);
    }
  }

  /**
   * Get current signer instance
   */
  getCurrentSigner(): ethers.Signer | null {
    return this.signer;
  }

  /**
   * Get network configuration
   */
  getNetworkConfig(network?: string): NetworkConfig {
    const networkKey = network || this.currentNetwork;
    const config = NETWORKS[networkKey];

    if (!config) {
      throw new Error(`Network configuration not found for: ${networkKey}`);
    }

    return config;
  }

  /**
   * Set current network
   */
  setCurrentNetwork(network: string): void {
    if (!NETWORKS[network]) {
      throw new Error(`Invalid network: ${network}`);
    }
    this.currentNetwork = network;
  }

  /**
   * Get current network
   */
  getCurrentNetwork(): string {
    return this.currentNetwork;
  }

  /**
   * Get network details from provider
   */
  async getNetwork(): Promise<ethers.Network> {
    const provider = this.getProvider();

    try {
      return await provider.getNetwork();
    } catch (error) {
      throw new Error(`Failed to get network: ${(error as Error).message}`);
    }
  }

  /**
   * Get block number
   */
  async getBlockNumber(): Promise<number> {
    const provider = this.getProvider();

    try {
      return await provider.getBlockNumber();
    } catch (error) {
      throw new Error(`Failed to get block number: ${(error as Error).message}`);
    }
  }

  /**
   * Get gas price
   */
  async getGasPrice(): Promise<bigint> {
    const provider = this.getProvider();

    try {
      const feeData = await provider.getFeeData();
      return feeData.gasPrice || 0n;
    } catch (error) {
      throw new Error(`Failed to get gas price: ${(error as Error).message}`);
    }
  }

  /**
   * Get fee data (EIP-1559)
   */
  async getFeeData(): Promise<ethers.FeeData> {
    const provider = this.getProvider();

    try {
      return await provider.getFeeData();
    } catch (error) {
      throw new Error(`Failed to get fee data: ${(error as Error).message}`);
    }
  }

  /**
   * Wait for transaction confirmation
   */
  async waitForTransaction(
    txHash: string,
    confirmations: number = 1
  ): Promise<ethers.TransactionReceipt | null> {
    const provider = this.getProvider();

    try {
      return await provider.waitForTransaction(txHash, confirmations);
    } catch (error) {
      throw new Error(`Failed to wait for transaction: ${(error as Error).message}`);
    }
  }

  /**
   * Reset provider state
   */
  reset(): void {
    this.provider = null;
    this.signer = null;
    this.currentNetwork = DEFAULT_NETWORK;
  }
}

// Singleton instance
export const web3Provider = new Web3Provider();

// Utility functions
export const formatEther = (value: bigint): string => {
  return ethers.formatEther(value);
};

export const parseEther = (value: string): bigint => {
  return ethers.parseEther(value);
};

export const formatUnits = (value: bigint, decimals: number): string => {
  return ethers.formatUnits(value, decimals);
};

export const parseUnits = (value: string, decimals: number): bigint => {
  return ethers.parseUnits(value, decimals);
};

export const isAddress = (address: string): boolean => {
  return ethers.isAddress(address);
};

export const getAddress = (address: string): string => {
  return ethers.getAddress(address);
};
