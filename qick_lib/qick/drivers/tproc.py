"""
Drivers for the QICK timed processor (tProc).
"""
from pynq.buffer import allocate
import numpy as np
from qick import SocIp

class AxisTProc64x32_x8(SocIp):
    """
    AxisTProc64x32_x8 class

    AXIS tProcessor registers:
    START_SRC_REG
    * 0 : internal start (using START_REG)
    * 1 : external start (using "start" input)

    Regardless of the START_SRC, the start logic triggers on a rising edge:
    A low level arms the trigger (transitions from "end" to "init" state).
    A high level fires the trigger (starts the program).
    To stop a running program, see reset().

    START_REG
    * 0 : init
    * 1 : start

    MEM_MODE_REG
    * 0 : AXIS Read (from memory to m0_axis)
    * 1 : AXIS Write (from s0_axis to memory)

    MEM_START_REG
    * 0 : Stop.
    * 1 : Execute operation (AXIS)

    MEM_ADDR_REG : starting memory address for AXIS read/write mode.

    MEM_LEN_REG : number of samples to be transferred in AXIS read/write mode.

    DMEM: The internal data memory is 2^DMEM_N samples, 32 bits each.
    The memory can be accessed either single read/write from AXI interface. The lower 256 Bytes are reserved for registers.
    The memory is then accessed in the upper section (beyond 256 bytes). Byte to sample conversion needs to be performed.
    The other method is to DMA in and out. Here the access is direct, so no conversion is needed.
    There is an arbiter to ensure data coherency and avoid blocking transactions.

    :param mem: memory address
    :type mem: int
    :param axi_dma: axi_dma address
    :type axi_dma: int
    """
    bindto = ['user.org:user:axis_tproc64x32_x8:1.0']
    REGISTERS = {'start_src_reg': 0,
                 'start_reg': 1,
                 'mem_mode_reg': 2,
                 'mem_start_reg': 3,
                 'mem_addr_reg': 4,
                 'mem_len_reg': 5}

    # Number of 32-bit words in the lower address map (reserved for register access)
    NREG = 64

    def __init__(self, description):
        """
        Constructor method
        """
        super().__init__(description)

        # Default registers.
        # start_src_reg = 0   : internal start.
        # start_reg     = 0   : stopped.
        # mem_mode_reg  = 0   : axis read.
        # mem_start_reg = 0   : axis operation stopped.
        # mem_addr_reg  = 0   : start address = 0.
        # mem_len_reg   = 100 : default length.
        self.start_src_reg = 0
        self.start_reg = 0
        self.mem_mode_reg = 0
        self.mem_start_reg = 0
        self.mem_addr_reg = 0
        self.mem_len_reg = 100

        # Generics.
        # data memory address size (log2 of the number of 32-bit words)
        self.DMEM_N = int(description['parameters']['DMEM_N'])
        # program memory address size (log2 of the number of 64-bit words, though the actual memory is usually smaller)
        self.PMEM_N = int(description['parameters']['PMEM_N'])

        self.cfg['dmem_size'] = 2**self.DMEM_N

    # Configure this driver with links to its memory and DMA.
    def configure(self, mem, axi_dma):
        # Program memory.
        self.mem = mem

        # dma
        self.dma = axi_dma

        self.cfg['pmem_size'] = self.mem.mmio.length//8

    def configure_connections(self, soc):
        self.cfg['output_pins'] = []
        self.cfg['start_pin'] = None
        self.cfg['f_time'] = soc.metadata.get_fclk(self.fullpath, "aclk")
        try:
            ((port),) = soc.metadata.trace_sig(self.fullpath, 'start')
            # check if the start pin is driven by a port of the top-level design
            if len(port)==1:
                self.cfg['start_pin'] = port[0]
        except:
            pass
        # search for the trigger port
        for iPort in range(8):
            # what block does this output drive?
            # add 1, because output 0 goes to the DMA
            try:
                ((block, port),) = soc.metadata.trace_bus(self.fullpath, 'm%d_axis' % (iPort+1))
            except: # skip disconnected tProc outputs
                continue
            if soc.metadata.mod2type(block) == "axis_set_reg":
                ((block, port),) = soc.metadata.trace_sig(block, 'dout')
                for iPin in range(16):
                    try:
                        ports = soc.metadata.trace_sig(block, "dout%d"%(iPin))
                        if len(ports)==1 and len(ports[0])==1:
                            # it's an FPGA pin, save it
                            pinname = ports[0][0]
                            self.cfg['output_pins'].append(('output', iPort, iPin, pinname))
                    except KeyError:
                        pass

    def port2ch(self, portname):
        """
        Translate a port name to a channel number.
        Used in connection mapping.
        """
        # port names are of the form 'm2_axis' (for outputs) and 's2_axis (for inputs)
        # subtract 1 to get the output channel number (s0/m0 goes to the DMA)
        chtype = {'m':'output', 's':'input'}[portname[0]]
        return int(portname.split('_')[0][1:])-1, chtype

    def start(self):
        """
        Start tProc from register.
        This has no effect if the tProc is not in init or end state,
        or if the start source is set to "external."
        """
        self.start_reg = 0
        self.start_reg = 1

    def reset(self):
        """
        Force the tProc to stop by filling the program memory with "end" instructions.
        For speed, we hard-code the "end" instruction and write directly to the program memory.
        This typically takes about 1 ms.
        """
        # we only write the high half of each program word, the low half doesn't matter
        np.copyto(self.mem.mmio.array[1::2],np.uint32(0x3F000000))

    def load_bin_program(self, binprog, reset=False):
        """
        Write the program to the tProc program memory.

        :param reset: Reset the tProc before writing the program.
        :type reset: bool
        """
        if reset: self.reset()

        # cast the program words to 64-bit uints
        self.binprog = np.array(binprog, dtype=np.uint64)
        # reshape to 32 bits to match the program memory
        self.binprog = np.frombuffer(self.binprog, np.uint32)

        self.reload_program()

    def reload_program(self):
        """
        Write the most recently written program to the tProc program memory.
        This is normally useful after a reset (which erases the program memory)
        """
        # write the program to memory with a fast copy
        np.copyto(self.mem.mmio.array[:len(self.binprog)], self.binprog)

    def start_src(self, src):
        """
        Sets the start source of tProc

        :param src: start source "internal" or "external"
        :type src: string
        """
        # set internal-start register to "init"
        # otherwise we might start the tProc on a transition from external to internal start
        self.start_reg = 0
        self.start_src_reg = {"internal": 0, "external": 1}[src]

    def single_read(self, addr):
        """
        Reads one sample of tProc data memory using AXI access

        :param addr: reading address
        :type addr: int
        :return: requested value
        :rtype: int
        """
        # Read data.
        # Address should be translated to upper map.
        return self.mmio.array[addr + self.NREG]

    def single_write(self, addr=0, data=0):
        """
        Writes one sample of tProc data memory using AXI access

        :param addr: writing address
        :type addr: int
        :param data: value to be written
        :type data: int
        """
        # Write data.
        # Address should be translated to upper map.
        self.mmio.array[addr + self.NREG] = np.uint32(data)

    def load_dmem(self, buff_in, addr=0):
        """
        Writes tProc data memory using DMA

        :param buff_in: Input buffer
        :type buff_in: int
        :param addr: Starting destination address
        :type addr: int
        """
        # Length.
        length = len(buff_in)

        # Configure dmem arbiter.
        self.mem_mode_reg = 1
        self.mem_addr_reg = addr
        self.mem_len_reg = length

        # Define buffer.
        self.buff = allocate(shape=length, dtype=np.int32)

        # Copy buffer.
        np.copyto(self.buff, buff_in)

        # Start operation on block.
        self.mem_start_reg = 1

        # DMA data.
        self.dma.sendchannel.transfer(self.buff)
        self.dma.sendchannel.wait()

        # Set block back to single mode.
        self.mem_start_reg = 0

    def read_dmem(self, addr=0, length=100):
        """
        Reads tProc data memory using DMA

        :param addr: Starting address
        :type addr: int
        :param length: Number of samples
        :type length: int
        :return: List of memory data
        :rtype: list
        """
        # Configure dmem arbiter.
        self.mem_mode_reg = 0
        self.mem_addr_reg = addr
        self.mem_len_reg = length

        # Define buffer.
        buff = allocate(shape=length, dtype=np.int32)

        # Start operation on block.
        self.mem_start_reg = 1

        # DMA data.
        self.dma.recvchannel.transfer(buff)
        self.dma.recvchannel.wait()

        # Set block back to single mode.
        self.mem_start_reg = 0

        return buff


class Axis_QICK_Proc(SocIp):
    """
    Axis_QICK_Proc class
    
    ####################
    AXIS T_PROC xREG
    ####################
    TPROC_CTRL       Write / Read 32-Bits
    TPROC_CFG        Write / Read 32-Bits
    MEM_ADDR         Write / Read 16-Bits
    MEM_LEN          Write / Read 16-Bits
    MEM_DT_I         Write / Read 32-Bits
    TPROC_W_DT1      Write / Read 32-Bits
    TPROC_W_DT2      Write / Read 32-Bits
    CORE_CFG         Write / Read 32-Bits
    READ_SEL         Write / Read 32-Bits
    MEM_DT_O         Read Only    32-Bits
    TPROC_R_DT1      Read Only    32-Bits
    TPROC_R_DT2      Read Only    32-Bits
    TIME_USR         Read Only    32-Bits
    TPROC_STATUS     Read Only    32-Bits
    TPROC_DEBUG      Read Only    32-Bits
    ####################
    TPROC_CTRL[0]  - Time Reset   : Reset absTimer
    TPROC_CTRL[1]  - Time Update  : Update absTimer
    TPROC_CTRL[2]  - Proc Start   : Reset and Starts tProc (Time and cores)
    TPROC_CTRL[3]  - Proc Stop    : Stop the tProc
    TPROC_CTRL[4]  - Core Start   : Reset and Starts the Cores.
    TPROC_CTRL[5]  - Core Stop    : Stop the Cores (Time will continue Running)
    TPROC_CTRL[6]  - Proc Reset   : Reset the TProc
    TPROC_CTRL[7]  - Proc Run     : Reset the TProc
    TPROC_CTRL[8]  - Proc Pause   : Pause the TProc (Time RUN, Core NO)
    TPROC_CTRL[9]  - Proc Freeze  : Freeze absTimer (Core RUN, Time no)
    TPROC_CTRL[10] - Proc Step    : Debug - Step tProc(Time and CORE )
    TPROC_CTRL[11] - Core Step    : Debug - Step Core  (Execute ONE instruction)
    TPROC_CTRL[12] - Time Step    : Debug - Step  Timer (Increase absTimer in 1)
    TPROC_CTRL[13] - COND_set     : Set External Condition Flag from
    TPROC_CTRL[14] - COND_clear   : Clears External Condition Flag from
    ####################
    TPROC_CFG[0]    - MEM_START
    TPROC_CFG[1]    - MEM_OPERATION
    TPROC_CFG[3:2]  - MEM_TYPE (00-NONE, 01-PMEM, 10-DMEM, 11-WMEM)
    TPROC_CFG[4]    - MEM_SOURCE (0-AXI, 1-SINGLE)
    TPROC_CFG[6:5]  - MEM_BANK (TPROC, CORE0, CORE1)
    TPROC_CFG[10]  - Disable INPUT CTRL
    TPROC_CFG[11]  - WFIFO_Full Pause Core
    TPROC_CFG[12]  - DFIFO_Full Pause Core
    
    
    :param mem: memory address
    :type mem: int
    :param axi_dma: axi_dma address
    :type axi_dma: int
    """
    bindto = ['Fermi:user:qick_processor:2.0']
    
    REGISTERS = {
        'tproc_ctrl'    :0 ,
        'tproc_cfg'     :1 ,
        'mem_addr'      :2 ,
        'mem_len'       :3 ,
        'mem_dt_i'      :4 ,
        'tproc_w_dt1'   :5,
        'tproc_w_dt2'   :6,
        'core_cfg'      :7,
        'read_sel'      :8,

        'mem_dt_o'      :10,
        'tproc_r_dt1'   :11 ,
        'tproc_r_dt2'   :12 ,
        'time_usr'      :13,
        'tproc_status'  :14,
        'tproc_debug'   :15
    }

    def __init__(self, description):
        """
        Constructor method
        """
        super().__init__(description)

        # Parameters
        #self.cfg['dual_core'] = = int(description['parameters']['DUAL_CORE'])
        #self.cfg['debug'] =     = int(description['parameters']['DEBUG'])
        # Parameters
        self.cfg['pmem_size'] = pow( 2, int(description['parameters']['PMEM_AW']) )
        self.cfg['dmem_size'] = pow( 2, int(description['parameters']['DMEM_AW']) )
        self.cfg['wmem_size'] = pow( 2, int(description['parameters']['WMEM_AW']) )
        self.cfg['dreg_qty']  = pow( 2, int(description['parameters']['REG_AW'])  )
        for param in ['in_port_qty', 'out_trig_qty', 'out_dport_qty','out_dport_dw', 'out_wport_qty']:
            self.cfg[param] = int(description['parameters'][param.upper()])
        for param in ['lfsr', 'divider', 'arith', 'time_read', 'tnet', 'custom_periph', 'io_ctrl']:
            self.cfg['has_'+param] = int(description['parameters'][param.upper()])


        # Initial Values 
        self.tproc_ctrl  = 0
        self.tproc_cfg   = 0
        self.mem_addr    = 0
        self.mem_len     = 0
        self.mem_dt_i    = 0
        self.tproc_w_dt1 = 0
        self.tproc_w_dt2 = 0
        self.core_cfg    = 0
        self.read_sel    = 0

        #Compatible with previous Version
        self.DMEM_N = int(description['parameters']['DMEM_AW']) 

    
    # Configure this driver with links to its memory and DMA.
    def configure(self, axi_dma):
        # dma
        self.dma = axi_dma

        # allocate DMA buffers, using the size of the largest memory
        maxlen = max(self['dmem_size'], self['pmem_size'], self['wmem_size'])
        self.buff_wr = allocate(shape=(maxlen, 8), dtype=np.int32)
        self.buff_rd = allocate(shape=(maxlen, 8), dtype=np.int32)

    
    def configure_connections(self, soc):
        self.cfg['output_pins'] = []
        self.cfg['start_pin'] = None
        self.cfg['f_core'] = soc.metadata.get_fclk(self.fullpath, "c_clk_i")
        self.cfg['f_time'] = soc.metadata.get_fclk(self.fullpath, "t_clk_i")
        try:
            ((port),) = soc.metadata.trace_sig(self.fullpath, 'start')
            self.start_pin = port[0]
        except:
            pass
        # WE have trig_%d_o and port_%d_dt_o as OUT of the QICK_PROCESSOR...
        # those can go to vec2bits or to the output...
        ## Number of triggers is in ther parameter 'out_trig_qty', the MAX is 8
        ## Number of data ports  is in ther parameter 'out_dport_qty', the MAX is 4
        for iPin in range(self['out_trig_qty']):
            try:
                ports = soc.metadata.trace_sig(self.fullpath, "trig_%d_o"%(iPin))
                if len(ports)==1 and len(ports[0])==1:
                    # it's an FPGA pin, save it
                    pinname = ports[0][0]
                    self.cfg['output_pins'].append(('trig', iPin, 0, pinname))
            except KeyError:
                pass
       # search for the trigger port
        for iPort in range(self['out_dport_qty']):
            # what block does this output drive?
            try:
                ((block, port),) = soc.metadata.trace_sig(self.fullpath, 'port_%d_dt_o' % (iPort))
            except: # skip disconnected tProc outputs
                continue
            if soc.metadata.mod2type(block) == "qick_vec2bit":
                n_outputs = int(soc.metadata.get_param(block, 'OUT_QTY'))
                for iPin in range(n_outputs):
                    try:
                        ports = soc.metadata.trace_sig(block, "dout%d"%(iPin))
                        if len(ports)==1 and len(ports[0])==1:
                            # it's an FPGA pin, save it
                            pinname = ports[0][0]
                            self.cfg['output_pins'].append(('dport', iPort, iPin, pinname))
                    except KeyError:
                        pass


    def port2ch(self, portname):
        """
        Translate a port name to a channel number and type
        Used in connection mapping.
        """
        words = portname.split('_')
        if words[-1] == 'axis':
            # port names are of the form 'm2_axis' (for outputs) and 's2_axis' (for inputs)
            chtype = {'m':'wport', 's':'input'}[words[0][0]]
            return int(words[0][1:]), chtype
        else:
            chtype = {'trig':'trig', 'port':'dport'}[words[0]]
            return int(words[1]), chtype

                    
    def time_reset(self):
        self.logger.info('TIME_RESET')
        self.tproc_ctrl      = 1
    def time_update(self):
        self.logger.info('TIME_UPDATE')
        self.tproc_ctrl      = 2
    def proc_start(self):
        self.logger.info('PROCESSOR_START')
        self.tproc_ctrl      = 4
    def proc_stop(self):
        self.logger.info('PROCESSOR_STOP')
        self.tproc_ctrl      = 8
    def core_start(self):
        self.logger.info('CORE_START')
        self.tproc_ctrl      = 16
    def core_stop(self):
        self.logger.info('CORE_STOP')
        self.tproc_ctrl      = 32
    def proc_reset(self):
        self.logger.info('PROCESSOR_RESET')
        self.tproc_ctrl      = 64
    def proc_run(self):
        self.logger.info('PROCESSOR_RUN')
        self.tproc_ctrl      = 128
    def proc_pause(self):
        self.logger.info('PROCESSOR_PAUSE')
        self.tproc_ctrl      = 256
    def proc_freeze(self):
        self.logger.info('PROCESSOR_FREEZE')
        self.tproc_ctrl      = 512
    def proc_step(self):
        self.logger.info('PROCESSOR_STEP')
        self.tproc_ctrl      = 1024
    def core_step(self):
        self.logger.info('CORE_STEP')
        self.tproc_ctrl      = 2048
    def time_step(self):
        self.logger.info('TIME_STEP')
        self.tproc_ctrl      = 4096
    def set_cond(self):
        self.logger.info('SET CONDITION')
        self.tproc_ctrl      = 8192
    def clear_cond(self):
        self.logger.info('CLEAR CONDITION')
        self.tproc_ctrl      = 16384

    def __str__(self):
        lines = []
        lines.append('---------------------------------------------')
        lines.append(' TPROC V2 INFO ')
        lines.append('---------------------------------------------')
        for param in ["pmem_size", "dmem_size", "wmem_size", "dreg_qty"]:
            lines.append("%-14s: %d" % (param, self.cfg[param]) )
        for param in ['in_port_qty', 'out_trig_qty', 'out_dport_qty','out_dport_dw', 'out_wport_qty']:
            lines.append("%-14s: %d" % (param, self.cfg[param]) )
        lines.append("\nConfiguration:")
        #for param in ['dual_core','debug', 'io_ctrl']:
        for param in ['has_io_ctrl']:
            lines.append("%-14s: %s" % (param, ["NO", "YES"][self.cfg[param]]))
        lines.append("\nPeripherals:")
        for param in ['has_lfsr', 'has_divider', 'has_arith', 'has_time_read', 'has_tnet', 'has_custom_periph']:
            lines.append("%-14s: %s" % (param, ["NO", "YES"][self.cfg[param]]))
        return "\n".join(lines)
    def info(self):
        print(self)

    def single_read(self, mem_sel, addr):
        """
        Reads the bottom 32 bits of one sample of tProc memory using AXI access
        Do not use! Use the DMA instead.
       
        :param addr: reading address
        :type addr: int
        :return: requested value
        :rtype: int
        """
        # Read data.
        self.mem_addr = i
        self.tproc_cfg = 0x11 + (mem_sel << 2)
        val = self.mem_dt_o
        self.tproc_cfg = 0
        return val
            
        def single_write(self, mem_sel, addr=0, data=0):
            """
            Writes the bottom 32 bits of one sample of tProc memory using AXI access
            Do not use! This seems to crash the DMA. Use the DMA instead.
            
            :param addr: writing address
            :type addr: int
            :param data: value to be written
            :type data: int
            """
            # Write data.
            self.mem_addr = i
            self.tproc_cfg = 0x13 + (mem_sel << 2)
            self.mem_dt_i = data
            self.tproc_cfg = 0
    
    def load_mem(self,mem_sel, buff_in, addr=0):
        """
        Writes tProc Selected memory using DMA

        Parameters
        ----------
        mem_sel : int
            PMEM=1, DMEM=2, WMEM=3
        buff_in : array
            Data to be loaded
        addr : int
            Starting write address
        """
        # Length.
        length = len(buff_in)
        # Configure Memory arbiter. (Write MEM)
        self.mem_addr        = addr
        self.mem_len         = length

        # Copy buffer.
        np.copyto(self.buff_wr[:length], buff_in)
        #Start operation
        if (mem_sel==1):       # WRITE PMEM
            self.tproc_cfg     |= 7
        elif (mem_sel==2):     # WRITE DMEM
            self.tproc_cfg     |= 11
        elif (mem_sel==3):     # WRITE WMEM
            self.tproc_cfg     |= 15
        else:
            raise RuntimeError('Destination Memeory error should be  PMEM=1, DMEM=2, WMEM=3 current Value : %d' % (mem_sel))

        # DMA data.
        self.logger.debug('DMA write 1')
        self.dma.sendchannel.transfer(self.buff_wr, nbytes=int(length*32))
        self.logger.debug('DMA write 2')
        self.dma.sendchannel.wait()
        self.logger.debug('DMA write 3')
        
        # End Operation
        self.tproc_cfg         &= ~63

    def read_mem(self,mem_sel, addr=0, length=100):
        """
        Read tProc Selected memory using DMA

        Parameters
        ----------
        mem_sel : int
            PMEM=1, DMEM=2, WMEM=3
        addr : int
            Starting read address
        length : int
            Number of words to read
        """
    # Configure Memory arbiter. (Read DMEM)
        self.mem_addr        = addr
        self.mem_len         = length

        #Start operation
        if (mem_sel==1):       # READ PMEM
            self.tproc_cfg     |= 5
        elif (mem_sel==2):     # READ DMEM
            self.tproc_cfg     |= 9
        elif (mem_sel==3):     # READ WMEM
            self.tproc_cfg     |= 13
        else:
            raise RuntimeError('Source Memeory error should be PMEM=1, DMEM=2, WMEM=3 current Value : %d' % (mem_sel))

        # DMA data.
        self.logger.debug('DMA read 1')
        self.dma.recvchannel.transfer(self.buff_rd, nbytes=int(length*32))
        self.logger.debug('DMA read 2')
        self.dma.recvchannel.wait()
        self.logger.debug('DMA read 3')
        
        # End Operation
        self.tproc_cfg         &= ~63

        # truncate and copy
        return self.buff_rd[:length].copy()

    def Load_PMEM(self, p_mem, check=True):
        length = len(p_mem)

        self.logger.info('Loading Program in PMEM')
        self.load_mem(1, p_mem)

        if check:
            readback = self.read_mem(1, length=length)
            if ( (np.max(readback - p_mem) )  == 0):
                self.logger.info('Program Loaded OK')
            else:
                self.logger.error('Error Loading Program')

        
    def get_axi(self):
        print('---------------------------------------------')
        print('--- AXI Registers')
        for xreg in self.REGISTERS.keys():
            print(f'{xreg:>15}', getattr(self, xreg))

    def get_proc_status(self):
        proc_st = ['T_RST','P_RST','RST_WAIT','T_INIT','STOP','PLAY','PAUSE','UPDATE','FREEZE','END_STEP']
        status_num = self.tproc_status
        status_bin = '{:032b}'.format(status_num)
        print('---------------------------------------------')
        print('--- AXI TPROC Register STATUS')
        print(status_bin)
        p_st = int(status_bin[29:32], 2)
        print('--- PROCESSOR -- ')
        print( 'PROC_ST         : ' + status_bin[29:32] +' - '+ proc_st[p_st])
        print( 'CORE_EN         : ' + status_bin[27] )
        print( 'TIME_EN         : ' + status_bin[26] )
        print( 'PROC_RST        : ' + status_bin[25] )
        print( 'EXT_COND        : ' + status_bin[23] )
        print( 'PORT_DT_NEW     : ' + status_bin[22] )
        print( 'FLAG_C0         : ' + status_bin[21] )
        print( 'ALL_DFIFO_EMPTY : ' + status_bin[19] )
        print( 'ALL_WFIFO_EMPTY : ' + status_bin[18] )
        print( 'ALL_DFIFO_FULL  : ' + status_bin[17] )
        print( 'ALL_WFIFO_FULL  : ' + status_bin[16] )
        print( 'DFIFO_FULL      : ' + status_bin[15] )
        print( 'WFIFO_FULL      : ' + status_bin[14] )
        print( 'FIFO_OK         : ' + status_bin[13] )
        print('--- MEMORY -- ')
        print( 'AW_EXEC         : ' + status_bin[4] )
        print( 'AR_EXEC         : ' + status_bin[3] )
        print( 'MEM_WE_SINGLE   : ' + status_bin[2] )
        print( 'MEM_OP          : ' + status_bin[0] )
    def get_proc_debug(self):
        debug_num = self.tproc_debug
        debug_bin = '{:032b}'.format(debug_num)
        print('---------------------------------------------')
        print('--- AXI TPROC Register DEBUG')
        print(debug_bin)
        print( 'USER_TIME           : ' + debug_bin[24:32] + ' - ' +str(int(debug_bin[24:32], 2)))
        print( 'REF_TIME            : ' + debug_bin[16:24] + ' - ' +str(int(debug_bin[16:24], 2)))
        print( 'EXT_MEM_W_DT_O[7:0] : ' + debug_bin[8 :16] + ' - ' +str(int(debug_bin[8:16], 2)))
        print( 'EXT_MEM_ADDR[7:0]   : ' + debug_bin[0 :8 ] + ' - ' +str(int(debug_bin[0:8], 2)))
    def get_core_status(self):
        status_num = self.core0_status
        status_bin = '{:032b}'.format(status_num)
        print('---------------------------------------------')
        print('--- AXI CORE Register STATUS')
        print(status_bin)
        p_st = int(status_bin[29:32], 2)
        print('--- PROCESSOR -- ')
        print( 'ARITH_DT_NEW    : ' + status_bin[31] )
        print( 'DIV_DT_NEW      : ' + status_bin[30] )
        print( 'TNET_DT_NEW     : ' + status_bin[29] )
        print( 'PERIPH_DT_NEW   : ' + status_bin[28] )
        print( 'ARITH_RDY       : ' + status_bin[27] )
        print( 'DIV_RDY         : ' + status_bin[26] )
        print( 'TNET_RDY        : ' + status_bin[25] )
        print( 'PERIPH_RDY      : ' + status_bin[24] )
        print( 'DFIFO_FULL      : ' + status_bin[23] )
        print( 'DFIFO_EMPTY     : ' + status_bin[22] )
        print( 'WFIFO_FULL      : ' + status_bin[21] )
        print( 'WFIFO_EMPTY     : ' + status_bin[20] )
    def get_core_debug(self):
        debug_num = self.core0_debug
        debug_bin = '{:032b}'.format(debug_num)
        print('---------------------------------------------')
        print('--- AXI TPROC Register DEBUG')
        print(debug_bin)
        print( 'PORT_O.P_TIME[7:0]           : ' + debug_bin[24:32] + ' - ' +str(int(debug_bin[24:32], 2)))
        print( 'R_X1_ALU_DT[7:0]            : ' + debug_bin[16:24] + ' - ' +str(int(debug_bin[16:24], 2)))
        print( 'ID_DMEM_WE : ' + debug_bin[8 :16] + ' - ' +str(int(debug_bin[8:16], 2)))
        print( 'ID_DREG_WE   : ' + debug_bin[0 :8 ] + ' - ' +str(int(debug_bin[0:8], 2)))
        print( 'ID_REG_WR   : ' + debug_bin[0 :8 ] + ' - ' +str(int(debug_bin[0:8], 2)))
        print( 'ID_MEM_WR   : ' + debug_bin[0 :8 ] + ' - ' +str(int(debug_bin[0:8], 2)))
        print( 'ID_REG_WR   : ' + debug_bin[0 :8 ] + ' - ' +str(int(debug_bin[0:8], 2)))
        print( 'ID_BRANCH   : ' + debug_bin[0 :8 ] + ' - ' +str(int(debug_bin[0:8], 2)))
        print( 'ID_DREG_WE   : ' + debug_bin[0 :8 ] + ' - ' +str(int(debug_bin[0:8], 2)))
        print( 'ID_CFG       : ' + debug_bin[0 :8 ] + ' - ' +str(int(debug_bin[0:8], 2)))
        print( 'ID_DREG_WE   : ' + debug_bin[0 :8 ] + ' - ' +str(int(debug_bin[0:8], 2)))
        
class Axis_QICK_Net(SocIp):
    """
    Axis_QICK_Proc class

    ####################
    AXIS T_CORE xREG
    ####################
    CORE_CTRL        Write / Read 32-Bits
    CORE_CFG         Write / Read 32-Bits
    RAXI_DT1         Write / Read 32-Bits
    RAXI_DT2         Write / Read 32-Bits
    CORE_R_DT1       Read Only    32-Bits
    CORE_R_DT2       Read Only    32-Bits
    PORT_LSW         Read Only    32-Bits
    PORT_MSW         Read Only    32-Bits
    RAND             Read Only    32-Bits
    CORE_W_DT1       Read Only    32-Bits
    CORE_W_DT2       Read Only    32-Bits
    CORE_STATUS      Read Only    32-Bits
    CORE_DEBUG       Read Only    32-Bits

    :param mem: memory address
    :type mem: int
    :param axi_dma: axi_dma address
    :type axi_dma: int
    """
    bindto = ['Fermi:user:qick_net:1.0']

    REGISTERS = {
        'tnet_ctrl'     :0 ,
        'tnet_cfg'      :1 ,
        'tnet_addr'     :2 ,
        'tnet_len'      :3 ,
        'raxi_dt1'      :4 ,
        'raxi_dt2'      :5 ,
        'raxi_dt3'      :6 ,
        'nn'            :7 ,
        'id'            :8,
        'cd'            :9,
        'rtd'           :10,
        'version'       :11,
        'tnet_w_dt1'    :12,
        'tnet_w_dt2'    :13,
        'tnet_status'   :14,
        'tnet_debug'    :15
    }

    def __init__(self, description):
        """
        Constructor method
        """
        super().__init__(description)

       
        # Initial Values 
        self.tnet_ctrl = 0
        self.tnet_cfg  = 0
        self.tnet_addr = 0
        self.mem_len   = 100
        self.tnet_len  = 0
        self.raxi_dt1  = 0
        self.raxi_dt2  = 0
        self.raxi_dt3  = 0

    # Configure this driver with links to its memory and DMA.
    def configure(self, mem, axi_dma):
        # Program memory.
        self.mem = mem
        # dma
        self.dma = axi_dma
    

    def clear_cond(self):
        self.logger.info('RESET')
        self.tproc_ctrl      = 2048
        
    def get_axi(self):
        print('---------------------------------------------')
        print('--- AXI Registers')
        for xreg in self.REGISTERS.keys():
            print(f'{xreg:>15}', getattr(self, xreg))
    def get_status(self):
        status_num = self.tnet_status
        status_bin = '{:032b}'.format(status_num)
        print('---------------------------------------------')
        print('--- AXI TNET Register STATUS')
        print(status_bin)
        print( ' MMC_LOCKED   : ' + status_bin[31] )
        print( ' GT_PLL_LOCK  : ' + status_bin[30] )
        print( ' LANE_A_UP    : ' + status_bin[29] )
        print( ' CHANNEL_A_UP : ' + status_bin[28] )
        print( ' CHANNEL_B_UP : ' + status_bin[27] )
        print( ' AURORA_RDY   : ' + status_bin[26] )
        print( ' AURORA_ST    : ' + str(int(status_bin[23:26],2)) )
        print( '--------------------------------')
        print( ' CMD_ID      : ' + status_bin[19:23] )
        print( ' CMD_DST      : ' + status_bin[16:19] )
        print( ' CMD_SRC      : ' + status_bin[13:16] )
        print( '--------------------------------')
        print( ' GET_NET      : ' + status_bin[10] )
        print( ' SET_NET      : ' + status_bin[9] )
        print( ' SYNC_NET     : ' + status_bin[8] )
        print( ' UPDT_OFF     : ' + status_bin[7] )
        print( ' RST_TPROC    : ' + status_bin[6] )
        print( ' START_TPROC  : ' + status_bin[5] )
        print( ' STOP_TPROC   : ' + status_bin[4] )
        print( ' SET_DT       : ' + status_bin[3] )
        print( ' GET_DT       : ' + status_bin[2] )
        print( ' SET_COND     : ' + status_bin[1] )
        print( ' CLEAR_COND   : ' + status_bin[0] )

    def get_debug(self):
        cmd_list = ['RST_0','>NET_GNET_P', '>NET_SYNC_P', '>NET_GNET_R', 'LOC_GNET', 'LOC_SNET', 'LOC_SYNC', 'LOC_UPDT_OFF',\
             'LOC_SET_DT', 'LOC_GET_DT', 'NET_GNET_P', 'NET_SNET_P', 'NET_SYNC_P', 'NET_UPDT_OFF_P', 'NET_SET_DT_P', 'NET_GET_DT_P',\
             'NET_GNET_R', 'NET_SNET_R', 'NET_SYNC_R', 'NET_UPDT_OFF_R', 'NET_SET_DT_R', 'NET_GET_DT_R', 'RST_1', 'RST_2',\
             'NET_GET_DT_A', 'NOT_READY', 'TIMEOUR', 'TX_ACK', 'CMD_nACK']
        task_list = ['NOT_READY','IDLE','LOC_CMD','LOC_WSYNC','LOC_SEND','LOC_WnREQ','NET_CMD','NET_WSYNC','NET_SEND','NET_WnREQ']
        debug_num = self.tnet_debug
        debug_bin = '{:032b}'.format(debug_num)
        task_st = int(debug_bin[19:23], 2) 
        cmd0_st = int(debug_bin[10:15], 2) 
        cmd1_st = int(debug_bin[5:10], 2) 
        cmd2_st = int(debug_bin[0:5], 2) 
        print('---------------------------------------------')
        print('--- AXI TNET Register DEBUG')
        print(debug_bin)
        print( ' AURORA_CNT  : ' + debug_bin[27:32] )
        print( ' AURORA_OP   : ' + debug_bin[23:27] )
        print( ' TASK_ST     : ' + str(task_st) + ' - ' + task_list[task_st])
        print( ' MAIN_ST     : ' + debug_bin[15:19] )
        #print( ' TASK_ST     : ' + debug_bin[19:23] )
        print( ' T0   : ' + str(cmd0_st) + ' - ' + cmd_list[cmd0_st])
        print( ' T1   : ' + str(cmd1_st) + ' - ' + cmd_list[cmd1_st])
        print( ' T2   : ' + str(cmd2_st) + ' - ' + cmd_list[cmd2_st])
        
    def get_sth(self):
        cmd_list = ['RST_0','>NET_GNET_P', '>NET_SYNC_P', '>NET_GNET_R', 'LOC_GNET', 'LOC_SNET', 'LOC_SYNC', 'LOC_UPDT_OFF',\
             'LOC_SET_DT', 'LOC_GET_DT', 'NET_GNET_P', 'NET_SNET_P', 'NET_SYNC_P', 'NET_UPDT_OFF_P', 'NET_SET_DT_P', 'NET_GET_DT_P',\
             'NET_GNET_R', 'NET_SNET_R', 'NET_SYNC_R', 'NET_UPDT_OFF_R', 'NET_SET_DT_R', 'NET_GET_DT_R', 'RST_1', 'RST_2',\
             'NET_GET_DT_A', 'NOT_READY', 'TIMEOUT', 'TX_ACK', 'CMD_nACK >> IDLE', 'ERROR', 'TX_nACK']
        task_list = ['NOT_READY','IDLE','LOC_CMD','LOC_WSYNC','LOC_SEND','LOC_WnREQ','NET_CMD','NET_WSYNC','NET_SEND','NET_WnREQ']
        debug_num = self.tnet_debug
        debug_bin = '{:032b}'.format(debug_num)
        task_st = int(debug_bin[19:23], 2) 
        ver_num = self.version
        ver_bin = '{:032b}'.format(ver_num)
        cmd0_st = int(ver_bin[25:30], 2) 
        cmd1_st = int(ver_bin[20:25], 2) 
        cmd2_st = int(ver_bin[15:20], 2) 
        cmd3_st = int(ver_bin[10:15], 2) 
        cmd4_st = int(ver_bin[5:10], 2) 
        cmd5_st = int(ver_bin[0:5], 2) 
        print('---------------------------------------------')
        print( ' AURORA_CNT  : ' + debug_bin[27:32] )
        print( ' AURORA_OP   : ' + debug_bin[23:27] )
        print( ' TASK_ST     : ' + str(task_st) + ' - ' + task_list[task_st])
        print( ' MAIN_ST     : ' + debug_bin[15:19] )
        print( ' T0   : ' + str(cmd0_st) + ' - ' + cmd_list[cmd0_st])
        print( ' T1   : ' + str(cmd1_st) + ' - ' + cmd_list[cmd1_st])
        print( ' T2   : ' + str(cmd2_st) + ' - ' + cmd_list[cmd2_st])
        print( ' T3   : ' + str(cmd3_st) + ' - ' + cmd_list[cmd3_st])
        print( ' T4   : ' + str(cmd4_st) + ' - ' + cmd_list[cmd4_st])
        print( ' T5   : ' + str(cmd5_st) + ' - ' + cmd_list[cmd5_st])
        
