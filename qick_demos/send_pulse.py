
# coding: utf-8

# In[ ]:


split_sequence_list = array_splitter(sequence_list)

config={"pulses": pulse_list,
        "sequences": split_sequence_list,   # TTL sequence
        "ro_chs":[0], # --Fixed
        "reps":number_of_loops, # --Fixed      #### this is set to 2 by default because if this is 1 then it does not send out pulse after the first trigger 
        "relax_delay":delay_time, # --us
        "readout_length": 1,
        "adc_trig_offset": 0, # [Clock ticks]
        # Try varying adc_trig_offset from 100 to 220 clock ticks

        # Try varying soft_avgs from 1 to 200 averages
       }

### this lambda deals with the lambda function used for pulse envelop
for j in range(0, len(config["pulses"])):
    if "lambda" in config["pulses"][j][-1]:
        config["pulses"][j][-1] = eval(config["pulses"][j][-1])
        
prog =LoopbackProgram(soccfg, config)
prog.load_pulses(soc)
prog.config_gens(soc)
prog.load_program(soc)