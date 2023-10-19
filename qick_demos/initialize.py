
# coding: utf-8

# In[ ]:


split_sequence_list = array_splitter(sequence_list)

config={"pulses": pulse_list,
        "sequences": split_sequence_list,
        "ro_chs":[0], # --Fixed
        "reps":number_of_loops, # --Fixed
        "relax_delay":delay_time, # --us
        "readout_length": 1,
        "adc_trig_offset": 0, # [Clock ticks]
        # Try varying adc_trig_offset from 100 to 220 clock ticks

        # Try varying soft_avgs from 1 to 200 averages
       }

for j in range(0, len(config["pulses"])):
    if "lambda" in config["pulses"][j][-1]:
        config["pulses"][j][-1] = eval(config["pulses"][j][-1])
        
prog =LoopbackProgram(soccfg, config)
prog.load_pulses(soc)
prog.config_gens(soc)
prog.load_program(soc)